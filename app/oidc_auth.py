from __future__ import annotations

import hashlib
import logging
import os
import secrets
from functools import wraps
from urllib.parse import urlencode, urlparse

from authlib.integrations.flask_client import OAuth
from flask import abort, flash, redirect, render_template, request, session, url_for

log = logging.getLogger("inventory-auth")
INTEGRATION_VERSION = "shared-auth-1"


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _cfg() -> dict:
    return {
        "enabled": _bool("AUTH_ENABLED", False),
        "issuer": os.getenv("OIDC_ISSUER", "").strip().rstrip("/"),
        "client_id": os.getenv("OIDC_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("OIDC_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.getenv("OIDC_REDIRECT_URI", "").strip(),
        "post_logout_redirect_uri": os.getenv("OIDC_POST_LOGOUT_REDIRECT_URI", "").strip(),
        "account_url": os.getenv("OIDC_ACCOUNT_URL", "").strip(),
        "auto_provision": _bool("OIDC_AUTO_PROVISION", True),
    }


def _missing(c: dict) -> list[str]:
    if not c["enabled"]:
        return []
    return [k for k in ("issuer", "client_id", "client_secret", "redirect_uri") if not c[k]]


def _safe_next(value: str | None) -> str:
    value = (value or "").strip()
    return value if value.startswith("/") and not value.startswith("//") else "/"


def _issuer_host(issuer: str) -> str:
    try:
        return urlparse(issuer).hostname or issuer
    except Exception:
        return issuer


def _popup_page(start_url: str, error: str = "") -> str:
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    error_html = f'<p class="error">{esc(error)}</p>' if error else ""
    return f"""<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'><title>Sign in</title>
<style>body{{font-family:system-ui;background:#f3f4f6;color:#111827;margin:0;display:grid;place-items:center;min-height:100vh}}.card{{width:min(90vw,420px);background:white;padding:28px;border-radius:18px;box-shadow:0 12px 36px #0002}}button{{width:100%;padding:14px;border:0;border-radius:12px;font-weight:800;cursor:pointer}}.error{{color:#b91c1c}}.muted{{color:#6b7280}}</style></head><body><section class=card><h1>Sign in</h1><p class=muted>Authentication is handled by Authentik. The sign-in window opens separately so this page stays in place.</p>{error_html}<button id=login>Continue with Authentik</button></section>
<script>document.getElementById('login').onclick=()=>{{const w=window.open({start_url!r},'inventory-auth','popup=yes,width=520,height=720,resizable=yes,scrollbars=yes');if(!w) location.href={start_url!r};}};window.addEventListener('message',e=>{{if(e.origin!==location.origin)return;if(e.data&&e.data.type==='inventory-auth-complete')location.href=e.data.next||'/';}});</script></body></html>"""


def _callback_page(next_url: str) -> str:
    return f"""<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'><title>Signed in</title></head><body style='font-family:system-ui;padding:24px'>Signed in. You can close this window.<script>(function(){{const data={{type:'inventory-auth-complete',next:{next_url!r}}};if(window.opener&&!window.opener.closed){{window.opener.postMessage(data,location.origin);window.close();}}else{{location.replace({next_url!r});}}}})();</script></body></html>"""


def configure(app, tenant, public_endpoints: set[str], initialise_owner_inventory):
    c = _cfg()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_bool("COOKIE_SECURE", False),
    )
    oauth = OAuth(app)
    oidc = None
    if c["enabled"] and not _missing(c):
        try:
            oidc = oauth.register(
                name="authentik",
                server_metadata_url=f"{c['issuer']}/.well-known/openid-configuration",
                client_id=c["client_id"],
                client_secret=c["client_secret"],
                client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256"},
            )
        except Exception as exc:
            log.error("OIDC client configuration failed: %s", exc.__class__.__name__)

    public_endpoints.update({"auth_start", "auth_callback", "auth_status", "account_recovery"})

    @app.before_request
    def oidc_fail_closed_and_csrf():
        endpoint = request.endpoint or ""
        if endpoint in {"health", "auth_status", "auth_start", "auth_callback", "login", "setup", "static"} or endpoint.startswith("static"):
            return None
        if c["enabled"] and (_missing(c) or oidc is None):
            return ("Authentication is enabled but OIDC is not configured correctly. Check OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET and OIDC_REDIRECT_URI.", 503)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin") or request.headers.get("Referer", "")
            if origin:
                try:
                    expected = request.host_url.rstrip("/")
                    actual = f"{urlparse(origin).scheme}://{urlparse(origin).netloc}"
                    if actual != expected:
                        abort(403, "Cross-site request rejected")
                except Exception:
                    abort(403, "Cross-site request rejected")
        return None

    def login_view():
        if not c["enabled"]:
            return ("Authentik authentication is required for Inventory Manager. Set AUTH_ENABLED=true and configure the OIDC environment variables.", 503)
        missing = _missing(c)
        error = f"Authentication configuration error: missing {', '.join(missing)}" if missing else ("Authentication provider could not be initialised." if oidc is None else "")
        next_url = _safe_next(request.args.get("next"))
        start = url_for("auth_start", next=next_url)
        return _popup_page(start, error), (503 if error else 200)

    def setup_view():
        return login_view()

    def new_account_view():
        return login_view()

    def account_settings_view():
        account = tenant.current_account()
        if not account:
            return redirect(url_for("login"))
        if request.method == "POST":
            action = request.form.get("action")
            if action == "business_name":
                tenant.update_business_name(account["id"], request.form.get("business_name", ""))
                flash("Business name updated.", "success")
            elif action in {"password", "change_password", "reset_password"}:
                return redirect(url_for("account_recovery"))
            return redirect(url_for("account_settings"))
        status = {
            "provider": "Authentik",
            "connected": bool(c["enabled"] and not _missing(c) and oidc is not None),
            "issuer_host": _issuer_host(c["issuer"]),
            "current_user": session.get("oidc_display_name") or session.get("oidc_email") or "",
            "integration_version": INTEGRATION_VERSION,
        }
        return render_template("account.html", account=account, auth_status=status)

    def logout_view():
        session.clear()
        target = c["post_logout_redirect_uri"] or url_for("login", _external=True)
        if oidc is not None:
            try:
                metadata = oidc.load_server_metadata()
                end_session = metadata.get("end_session_endpoint")
                if end_session:
                    return redirect(end_session + "?" + urlencode({"client_id": c["client_id"], "post_logout_redirect_uri": target}))
            except Exception as exc:
                log.warning("OIDC logout discovery failed: %s", exc.__class__.__name__)
        return redirect(target)

    app.view_functions["login"] = login_view
    app.view_functions["setup"] = setup_view
    app.view_functions["new_owner_account"] = new_account_view
    app.view_functions["account_settings"] = account_settings_view
    app.view_functions["logout"] = logout_view

    @app.get("/auth/start")
    def auth_start():
        if not c["enabled"] or _missing(c) or oidc is None:
            return login_view()
        session["post_auth_next"] = _safe_next(request.args.get("next"))
        nonce = secrets.token_urlsafe(24)
        session["oidc_nonce"] = nonce
        return oidc.authorize_redirect(c["redirect_uri"], nonce=nonce)

    @app.get("/auth/callback")
    def auth_callback():
        if not c["enabled"] or _missing(c) or oidc is None:
            return login_view()
        try:
            token = oidc.authorize_access_token()
            user = token.get("userinfo")
            if not user:
                user = oidc.parse_id_token(token, nonce=session.pop("oidc_nonce", None))
            subject = str(user.get("sub", "")).strip()
            if not subject:
                raise ValueError("OIDC subject missing")
            account = tenant.link_oidc_identity(
                auth_subject=subject,
                email=str(user.get("email", "")).strip(),
                display_name=str(user.get("name") or user.get("preferred_username") or user.get("email") or "").strip(),
                auto_provision=c["auto_provision"],
            )
            if not account:
                return _popup_page(url_for("auth_start"), "This Authentik identity has not been provisioned for Inventory Manager."), 403
            next_url = session.pop("post_auth_next", "/")
            session.clear()
            session["account_id"] = account["id"]
            session["oidc_subject_fingerprint"] = hashlib.sha256(subject.encode()).hexdigest()[:12]
            session["oidc_email"] = account["email"]
            session["oidc_display_name"] = account["display_name"] or account["email"]
            initialise_owner_inventory()
            return _callback_page(next_url)
        except Exception as exc:
            log.warning("OIDC login failed: %s", exc.__class__.__name__)
            return _popup_page(url_for("auth_start"), "Sign-in failed. Please try again or check the Authentik configuration."), 401

    @app.get("/auth/account")
    def account_recovery():
        if not c["enabled"]:
            return login_view()
        if c["account_url"]:
            return redirect(c["account_url"])
        return redirect(c["issuer"] or url_for("login"))

    @app.get("/auth/status")
    def auth_status():
        return {
            "provider": "Authentik",
            "enabled": c["enabled"],
            "connected": bool(c["enabled"] and not _missing(c) and oidc is not None),
            "configuration_error": bool(c["enabled"] and (_missing(c) or oidc is None)),
            "issuer_hostname": _issuer_host(c["issuer"]),
            "signed_in": bool(tenant.current_account()),
            "integration_version": INTEGRATION_VERSION,
        }
