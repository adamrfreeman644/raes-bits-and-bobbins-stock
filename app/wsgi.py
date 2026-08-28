"""Production entrypoint with migration-safe Authentik activation."""

import os

from app.main import app, initialise_owner_inventory, tenant, PUBLIC_ENDPOINTS
from app import server
from app.custom_fields import configure as configure_custom_fields


configure_custom_fields(app, server)


def enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


# Existing installations keep their current local login during the one-time
# migration/rollback window. Once the owner deliberately enables Authentik,
# the OIDC layer becomes authoritative and fails closed if misconfigured.
if enabled("AUTH_ENABLED"):
    from app.oidc_auth import configure as configure_oidc
    configure_oidc(app, tenant, PUBLIC_ENDPOINTS, initialise_owner_inventory)