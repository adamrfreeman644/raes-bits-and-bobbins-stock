# Inventory Manager v0.3.0

Self-hosted multi-account stock and sales manager designed for barcode-scanner Android devices, tablets and desktop browsers.

## Security model

Inventory Manager is a multi-account data application. From v0.3.0, normal user authentication is delegated to **Authentik using OpenID Connect (OIDC)**. Inventory Manager does not create or store Authentik passwords. Authentik handles sign-in, password reset/recovery, MFA, account disabling and passkeys/WebAuthn when enabled there.

The browser's main Inventory Manager page is not sent away during normal sign-in. The **Continue with Authentik** button opens Authentik in a small popup window. The OIDC callback completes inside that popup, then the popup closes and the original Inventory Manager page resumes. If a browser blocks popups, a normal same-tab redirect is used as a compatibility fallback.

Each local account retains its own inventory database, photos and backups. Authenticated identities are permanently mapped with the OIDC `sub` claim; email is profile information only and is not the permanent identity key. Tenant selection happens on the server before database paths are resolved, so frontend filtering cannot grant access to another account's inventory.

### Authentik setup

Create an OAuth2/OpenID Provider and Application in Authentik for Inventory Manager. Use Authorization Code flow and a confidential client. Set the redirect URI exactly to the externally reachable callback, for example:

```text
https://inventory.example.com/auth/callback
```

Copy `.env.example` to your deployment environment and set:

```text
AUTH_ENABLED=true
OIDC_ISSUER=https://auth.example.com/application/o/inventory-manager
OIDC_CLIENT_ID=inventory-manager
OIDC_CLIENT_SECRET=<secret from Authentik>
OIDC_REDIRECT_URI=https://inventory.example.com/auth/callback
OIDC_POST_LOGOUT_REDIRECT_URI=https://inventory.example.com/login
OIDC_ACCOUNT_URL=https://auth.example.com/if/user/
COOKIE_SECURE=true
```

Do not put the client secret in Git. Use the host `.env`, Docker secrets, or your existing secure container configuration. `OIDC_ISSUER` is the issuer shown by the Authentik provider; discovery is loaded from its standard `.well-known/openid-configuration` endpoint.

### First login and existing accounts

This is a non-destructive migration. Existing account rows, inventory databases, photos, backups and updater data are kept. On the first successful Authentik login, Inventory Manager first looks for the immutable OIDC `sub`. If it has not been linked yet, an existing local account with the same email can be linked once. After that, `sub` is authoritative even if the email changes.

If `OIDC_AUTO_PROVISION=true`, a previously unknown Authentik identity receives a new isolated local Inventory Manager tenant. No second password is requested. Set `OIDC_AUTO_PROVISION=false` if only pre-linked identities should be admitted.

Legacy password hashes are retained only as rollback/migration data for existing installations. Active v0.3.0 OIDC sign-in does not verify or create local passwords.

### Password reset, recovery and logout

Use **Password & account security** in the Owner Account page for password changes, reset, recovery, MFA and passkeys; it opens the configured Authentik account page. Inventory Manager never displays or recovers an existing password.

**Log out** clears the local application session and uses the provider's OIDC end-session endpoint when Authentik advertises one, then returns to the configured post-logout page.

### Failure behaviour and troubleshooting

When `AUTH_ENABLED=true`, a missing/invalid OIDC configuration **fails closed**: protected inventory routes return a useful configuration error instead of silently disabling authentication. `/health` remains suitable for container monitoring and does not disclose credentials, tokens or user data.

Check `/auth/status` for non-secret integration state. The Owner Account page shows provider, connected/configuration-error state, issuer hostname, signed-in user and integration version. It never displays client secrets or OIDC tokens.

For HTTPS deployments set `COOKIE_SECURE=true`. If sign-in opens but does not finish, first verify the exact callback URI, issuer URL, reverse-proxy HTTPS headers and browser popup policy.

## Install

```bash
git clone https://github.com/adamrfreeman644/raes-bits-and-bobbins-stock.git
cd raes-bits-and-bobbins-stock
cp .env.example .env
# Configure Authentik/OIDC and other deployment values.
docker compose up -d --build
```

Open `http://SERVER-IP:1975` on a trusted LAN or your HTTPS reverse-proxy URL. Health check: `http://SERVER-IP:1975/health`.

## Persistent data

- `./data/` — platform database plus isolated account databases
- `./photos/` — isolated account photo libraries and preserved crop originals
- `./backups/` — automatic, manual, updater and pre-restore database backups
- `./updater-state/` — updater status and log files

These mounts are separate from the application image, so rebuilding or updating the container does not replace inventory data.

## Existing inventory features

The release preserves parent products with unique physical-item barcodes, quantity tracking, PayPal POS CSV import/export, dashboards, permanent sales history, events/pop-up shops, activity/undo, barcode search, archive/restore, automatic/manual backups, photo management, Photo Shoot workflow and existing inventory/update behaviour.

## Updating

The updater still checks GitHub's `VERSION` file but does not silently install releases. Installation remains a deliberate action in the web interface, and updater backups remain in place. Authentication is applied around the application rather than replacing the updater implementation.

### One-time v0.3.0 upgrade steps

1. Back up the existing appdata directory/database as normal.
2. Create the Inventory Manager Provider/Application in Authentik and register the exact callback URI.
3. Add the OIDC environment variables without committing secrets.
4. Set `COOKIE_SECURE=true` when the public application URL is HTTPS.
5. Upgrade/rebuild Inventory Manager.
6. Sign in through the popup with the Authentik identity whose email matches the existing owner account for the one-time link.
7. Confirm the Owner Account authentication status is **Connected** and verify existing stock/photos before removing any external rollback backup.
