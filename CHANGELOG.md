# Changelog

## 0.3.3 — Preserve persistent data mounts during updates

- Fix updater-triggered container recreation using the wrong host bind paths.
- Discover the real host path backing `/project` from Docker before recreating Inventory Manager.
- Pass that host path into Compose for data, photos, backups and updater-state mounts.
- Prevent updates from accidentally mounting host `/project/*` directories instead of the Unraid appdata directories.
- Preserve existing owner accounts and tenant inventory databases across updater rebuilds.

## 0.3.2 — Reliable update checks and compact menu

- Move GitHub version checking into the updater container instead of the web container.
- Store the latest detected version in shared updater state for the webpage to read locally.
- Make Check Again explicitly ask the updater service to fetch `origin/main`.
- Keep update installation backup-first and preserve all tenant data.
- Reduce the hamburger button and menu width, spacing and mobile row height.
- Hide menu descriptions on small screens so navigation stays compact.

## 0.3.1 — Per-account inventory fields

- Add account-specific optional inventory fields without changing the existing core field set.
- Add built-in optional fields for Board compatibility, Cost, Supplier, Manufacturer, Model / part number, Storage location, Condition and Reorder level.
- Keep all optional fields disabled by default so existing accounts retain their current layout.
- Add custom field creation with Text, Number, Money (£), Yes / No and Dropdown field types.
- Show enabled fields on Add Product, Edit Product, Duplicate Product and Product Detail views.
- Store field definitions and values inside each tenant's isolated inventory database.
- Allow custom fields to be deleted and built-in optional fields to be disabled without modifying the core product schema.

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
