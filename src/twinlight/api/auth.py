"""``GET /internal/whoami`` — which token, if any, a request authenticated as.

Not part of T-API. It exists so a client can check its credentials without
side effects or a large response: ``/health`` is the one public path, so it
cannot tell a client whether its token is accepted, and every T-API resource
is either large (the context) or topology-specific. The web UI uses it as its
authenticated connection probe.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/whoami")
async def whoami(request: Request) -> dict[str, str | None]:
    """Report the caller's principal and whether authentication is on.

    With authentication disabled, ``principal`` is null and
    ``authentication`` is ``"disabled"``. With it enabled, an unauthenticated
    request never reaches this handler — the middleware answers 401 — so a
    200 always names the token the request carried.
    """
    user = request.scope.get("user")  # set by TokenAuthMiddleware, if installed
    if user is None:
        return {"principal": None, "authentication": "disabled"}
    return {"principal": user.display_name, "authentication": "bearer-token"}
