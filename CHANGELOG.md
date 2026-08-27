# Changelog

## 0.3.0 — Shared authentication architecture

- Add Authentik OpenID Connect authentication using Authorization Code flow with PKCE support.
- Keep sign-in in a popup window so the main Inventory Manager page remains in place.
- Map local tenants by immutable OIDC `sub`, with one-time legacy email linking and optional isolated auto-provisioning.
- Preserve existing tenant databases, photos, backups, settings and updater state non-destructively.
- Delegate password reset, recovery, MFA, passkeys and account disabling to Authentik; active OIDC accounts do not store local passwords.
- Fail protected access closed when Authentik is enabled but OIDC configuration is incomplete.
- Add non-secret authentication status information and OIDC-aware logout.
- Preserve the legacy local login only while `AUTH_ENABLED=false` for migration/rollback.
- Add automated authentication/tenant isolation checks and Compose validation.
