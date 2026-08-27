"""Production entrypoint.

Imports the existing application unchanged, then installs the coordinated
Authentik/OIDC layer. Keeping this as a thin wrapper makes rollback safe and
avoids disturbing the existing inventory/update route registration order.
"""

from app.main import app, initialise_owner_inventory, tenant, PUBLIC_ENDPOINTS
from app.oidc_auth import configure as configure_oidc

configure_oidc(app, tenant, PUBLIC_ENDPOINTS, initialise_owner_inventory)
