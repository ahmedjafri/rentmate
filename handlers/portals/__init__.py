"""Tenant and vendor portal HTTP handlers.

Public-facing REST endpoints used by the tenant and vendor portals in the SPA.
Each portal has two modules:

  - ``{entity}_portal`` — authenticated endpoints (require a portal JWT)
  - ``{entity}_invite`` — unauthenticated token exchange (portal_token → JWT)

Shared helpers live in ``_common``.
"""
