"""Production entrypoint with migration-safe Authentik activation."""

import os

from flask import flash, redirect, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from app.main import app, initialise_owner_inventory, tenant, PUBLIC_ENDPOINTS
from app import server
from app.custom_fields import configure as configure_custom_fields
from app.dashboard_recent import configure as configure_dashboard_recent
from app.barcode_fixes import configure as configure_barcode_fixes
from app.table_barcode_fix import configure as configure_table_barcode_fix


configure_custom_fields(app, server)
configure_dashboard_recent(app, server)
configure_barcode_fixes(app, server)
configure_table_barcode_fix(app, server)

# Photo Shoot deliberately supports large multi-image batches. The original
# 40 MB Flask limit was suitable for individual product photos but rejected a
# normal phone photo batch before the Photo Shoot route ever saw it.
try:
    max_upload_mb = max(40, int(os.getenv('MAX_UPLOAD_MB', '512')))
except ValueError:
    max_upload_mb = 512
app.config['MAX_CONTENT_LENGTH'] = max_upload_mb * 1024 * 1024


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(_error):
    flash(f'That photo batch is larger than the {max_upload_mb} MB upload limit. Select a smaller batch or raise MAX_UPLOAD_MB.', 'error')
    return redirect(url_for('photoshoot.photo_shoot'))


def enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


# Existing installations keep their current local login during the one-time
# migration/rollback window. Once the owner deliberately enables Authentik,
# the OIDC layer becomes authoritative and fails closed if misconfigured.
if enabled("AUTH_ENABLED"):
    from app.oidc_auth import configure as configure_oidc
    configure_oidc(app, tenant, PUBLIC_ENDPOINTS, initialise_owner_inventory)