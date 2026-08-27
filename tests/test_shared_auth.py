import importlib
import sqlite3

import pytest


@pytest.fixture()
def tenant_module(tmp_path, monkeypatch):
    import app.tenant as tenant
    monkeypatch.setattr(tenant, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(tenant, "PHOTO_ROOT", tmp_path / "photos")
    monkeypatch.setattr(tenant, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(tenant, "PLATFORM_DB", tmp_path / "data" / "platform.db")
    for path in (tenant.DATA_DIR, tenant.PHOTO_ROOT, tenant.BACKUP_ROOT, tenant.DATA_DIR / "accounts"):
        path.mkdir(parents=True, exist_ok=True)
    tenant.init_platform()
    return tenant


def test_oidc_subject_becomes_permanent_identity(tenant_module):
    tenant = tenant_module
    legacy_id = tenant.create_account("owner@example.test", "rollback-only-password", "Existing Business")
    linked = tenant.link_oidc_identity("authentik-sub-123", "owner@example.test", "Owner", auto_provision=False)
    assert linked["id"] == legacy_id
    assert linked["auth_subject"] == "authentik-sub-123"

    # Email may change in the identity provider; `sub`, not email, resolves tenant.
    again = tenant.link_oidc_identity("authentik-sub-123", "new@example.test", "Owner New", auto_provision=False)
    assert again["id"] == legacy_id
    assert again["email"] == "new@example.test"


def test_auto_provisioned_accounts_have_isolated_storage(tenant_module):
    tenant = tenant_module
    a = tenant.link_oidc_identity("sub-a", "a@example.test", "A", auto_provision=True)
    b = tenant.link_oidc_identity("sub-b", "b@example.test", "B", auto_provision=True)
    assert a["id"] != b["id"]
    assert tenant.account_db_path(a["id"]) != tenant.account_db_path(b["id"])
    assert tenant.account_photo_dir(a["id"]) != tenant.account_photo_dir(b["id"])
    assert tenant.account_backup_dir(a["id"]) != tenant.account_backup_dir(b["id"])


def test_oidc_only_account_does_not_store_a_password(tenant_module):
    tenant = tenant_module
    account = tenant.create_oidc_account("sub-no-password", "oidc@example.test", "OIDC Owner")
    row = tenant.get_account(account["id"])
    assert row["password_hash"] == "!oidc-only"
    assert "oidc@example.test" not in row["password_hash"]


def test_duplicate_subject_cannot_map_to_two_tenants(tenant_module):
    tenant = tenant_module
    first = tenant.create_oidc_account("same-sub", "one@example.test", "One")
    second = tenant.link_oidc_identity("same-sub", "two@example.test", "Two", auto_provision=True)
    assert second["id"] == first["id"]
    with tenant.platform_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts WHERE auth_subject='same-sub'").fetchone()[0] == 1


def test_auth_enabled_with_missing_oidc_config_is_detected(monkeypatch):
    import app.oidc_auth as oidc
    monkeypatch.setenv("AUTH_ENABLED", "true")
    for name in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI"):
        monkeypatch.delenv(name, raising=False)
    cfg = oidc._cfg()
    assert cfg["enabled"] is True
    assert set(oidc._missing(cfg)) == {"issuer", "client_id", "client_secret", "redirect_uri"}


def test_popup_login_keeps_main_page_available():
    import app.oidc_auth as oidc
    page = oidc._popup_page("/auth/start?next=/dashboard")
    assert "window.open" in page
    assert "inventory-auth-complete" in page
