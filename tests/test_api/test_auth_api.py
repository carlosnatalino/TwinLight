"""Bearer-token authentication on the REST interface.

The twin authenticates when tokens are configured and is open when none are;
the second half of that is what every other test module exercises, so this
one builds a twin *with* tokens and checks the contract a client relies on:
RFC 6750 challenges, an RFC 8040 error body, /health as the only public path,
CORS that still works, and an OpenAPI schema that says what the middleware
does — and, with no tokens, that the middleware is not installed at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from twinlight.api.middleware import TokenAuthMiddleware
from twinlight.app import PUBLIC_PATHS, create_app
from twinlight.auth import parse_tokens
from twinlight.config import TwinConfig

UI = "ui-token-0123456789abcdef"
OPS = "ops-token-0123456789abcdef"
SIPS = "/restconf/data/tapi-common:context/service-interface-point"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def secured(twin_config: TwinConfig) -> TestClient:
    config = twin_config.model_copy(
        update={"api_tokens": parse_tokens(f"ui:{UI},ops:{OPS}")}
    )
    return TestClient(create_app(config))


class TestRejection:
    def test_no_credentials_is_401_with_a_bare_challenge(
        self, secured: TestClient
    ) -> None:
        resp = secured.get(SIPS)
        assert resp.status_code == 401
        # RFC 6750 §3.1: no error code when no credentials were offered.
        assert resp.headers["www-authenticate"] == 'Bearer realm="twinlight"'

    def test_wrong_token_is_401_invalid_token(self, secured: TestClient) -> None:
        resp = secured.get(SIPS, headers=_bearer("not-a-real-token-0000000"))
        assert resp.status_code == 401
        assert 'error="invalid_token"' in resp.headers["www-authenticate"]

    def test_other_scheme_is_treated_as_no_credentials(
        self, secured: TestClient
    ) -> None:
        resp = secured.get(SIPS, headers={"Authorization": f"Basic {UI}"})
        assert resp.status_code == 401
        assert "error=" not in resp.headers["www-authenticate"]

    def test_body_is_a_restconf_access_denied_error(
        self, secured: TestClient
    ) -> None:
        resp = secured.get(SIPS)
        assert resp.headers["content-type"].startswith("application/yang-data+json")
        (error,) = resp.json()["ietf-restconf:errors"]["error"]
        assert error["error-tag"] == "access-denied"  # RFC 8040 §7
        assert error["error-type"] == "protocol"
        assert "Bearer" in error["error-message"]

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/data/tapi-common:context"),
            ("GET", "/restconf"),
            ("GET", "/internal/opm"),
            ("GET", "/internal/whoami"),
            ("GET", "/metrics"),
            # The discovery and documentation resources are not exempt either.
            ("GET", "/.well-known/host-meta"),
            ("GET", "/docs"),
            ("GET", "/redoc"),
            ("GET", "/openapi.json"),
            ("GET", "/transparent/data"),
            ("GET", "/data/tapi-common:context/tapi-streaming:stream-context"),
            ("GET", "/data/tapi-common:context/tapi-oam:oam-context"),
            ("POST", "/admin/snapshot"),
            ("POST", "/config/parameters"),
            ("DELETE", (
                "/data/tapi-connectivity:connectivity-context"
                "/connectivity-service=x"
            )),
            # Unknown paths too: a 404 would map the API for an anonymous probe.
            ("GET", "/no/such/route"),
            # Exact matches only.
            ("GET", "/health/"),
            ("GET", "/healthz"),
        ],
    )
    def test_everything_off_the_allowlist_is_protected(
        self, secured: TestClient, method: str, path: str
    ) -> None:
        assert secured.request(method, path).status_code == 401

    def test_a_rejected_request_has_no_side_effect(
        self, secured: TestClient
    ) -> None:
        snapshot_dir = secured.app.state.config.simulation.snapshot_dir  # type: ignore[attr-defined]

        def written() -> list[str]:
            return sorted(p.name for p in snapshot_dir.glob("*.json")) if snapshot_dir.is_dir() else []

        assert secured.post("/admin/snapshot").status_code == 401
        assert written() == []
        # The same request with a token does write, so the check above can fail.
        assert secured.post("/admin/snapshot", headers=_bearer(OPS)).status_code == 200
        assert len(written()) == 1


class TestAcceptance:
    def test_valid_token_reaches_the_route(self, secured: TestClient) -> None:
        resp = secured.get(SIPS, headers=_bearer(UI))
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/yang-data+json")

    def test_every_configured_token_is_accepted(self, secured: TestClient) -> None:
        for token in (UI, OPS):
            assert secured.get(SIPS, headers=_bearer(token)).status_code == 200

    def test_scheme_is_case_insensitive(self, secured: TestClient) -> None:
        resp = secured.get(SIPS, headers={"Authorization": f"bearer {UI}"})
        assert resp.status_code == 200

    def test_percent_encoded_paths_still_route(self, secured: TestClient) -> None:
        resp = secured.get(
            "/restconf/data/tapi-common%3Acontext/service-interface-point",
            headers=_bearer(UI),
        )
        assert resp.status_code == 200

    def test_404_after_authentication(self, secured: TestClient) -> None:
        resp = secured.get("/no/such/route", headers=_bearer(UI))
        assert resp.status_code == 404


class TestPublicPaths:
    def test_only_health_is_public(self) -> None:
        assert PUBLIC_PATHS == frozenset({"/health"})

    def test_health_is_served_without_a_token(self, secured: TestClient) -> None:
        assert secured.get("/health").status_code == 200

    @pytest.mark.parametrize("path", ["/.well-known/host-meta", "/docs", "/openapi.json"])
    def test_discovery_and_docs_work_with_a_token(
        self, secured: TestClient, path: str
    ) -> None:
        assert secured.get(path, headers=_bearer(UI)).status_code == 200

    def test_a_bad_token_on_a_public_path_is_ignored(
        self, secured: TestClient
    ) -> None:
        resp = secured.get("/health", headers=_bearer("garbage-garbage-garbage"))
        assert resp.status_code == 200


class TestWhoami:
    def test_names_the_principal(self, secured: TestClient) -> None:
        assert secured.get("/internal/whoami", headers=_bearer(OPS)).json() == {
            "principal": "ops",
            "authentication": "bearer-token",
        }

    def test_reports_disabled_when_open(self, app: TestClient) -> None:
        assert app.get("/internal/whoami").json() == {
            "principal": None,
            "authentication": "disabled",
        }


class TestDisabled:
    def test_no_tokens_means_no_middleware(self, twin_config: TwinConfig) -> None:
        """Authentication off: the filter is not in the stack, so it never runs."""
        app = create_app(twin_config)
        assert TokenAuthMiddleware not in {m.cls for m in app.user_middleware}
        secured = create_app(
            twin_config.model_copy(update={"api_tokens": parse_tokens(UI)})
        )
        assert TokenAuthMiddleware in {m.cls for m in secured.user_middleware}

    def test_no_tokens_means_open(self, app: TestClient) -> None:
        assert app.get(SIPS).status_code == 200

    def test_a_stray_header_is_ignored(self, app: TestClient) -> None:
        """A client configured with a token can talk to an open twin."""
        assert app.get(SIPS, headers=_bearer("whatever-whatever-1")).status_code == 200

    def test_openapi_declares_no_security(self, app: TestClient) -> None:
        schema = app.get("/openapi.json").json()
        assert "security" not in schema
        assert "securitySchemes" not in schema.get("components", {})


class TestCors:
    ORIGIN = "http://localhost:3000"  # in ServerConfig's default cors_origins

    def test_preflight_needs_no_token(self, secured: TestClient) -> None:
        resp = secured.options(
            SIPS,
            headers={
                "Origin": self.ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert resp.status_code == 200
        # Fetch never lets "*" cover Authorization; it has to be named.
        allowed = resp.headers["access-control-allow-headers"].lower()
        assert "authorization" in allowed

    def test_401_carries_cors_headers(self, secured: TestClient) -> None:
        """Without them the browser hides the 401 and the UI sees a network error."""
        resp = secured.get(SIPS, headers={"Origin": self.ORIGIN})
        assert resp.status_code == 401
        assert resp.headers["access-control-allow-origin"] == self.ORIGIN


class TestOpenApi:
    def test_declares_the_bearer_scheme(self, secured: TestClient) -> None:
        schema = secured.get("/openapi.json", headers=_bearer(UI)).json()
        scheme = schema["components"]["securitySchemes"]["bearerAuth"]
        assert scheme == {**scheme, "type": "http", "scheme": "bearer"}
        assert schema["security"] == [{"bearerAuth": []}]

    def test_public_operations_are_marked_open(self, secured: TestClient) -> None:
        paths = secured.get("/openapi.json", headers=_bearer(UI)).json()["paths"]
        assert paths["/health"]["get"]["security"] == []
        assert "security" not in paths["/internal/whoami"]["get"]

    def test_schema_is_stable_across_requests(self, secured: TestClient) -> None:
        first = secured.get("/openapi.json", headers=_bearer(UI)).json()
        assert secured.get("/openapi.json", headers=_bearer(UI)).json() == first


class TestHttpExceptionHeaders:
    def test_headers_survive_the_restconf_error_handler(self, app: TestClient) -> None:
        """The handler used to drop exc.headers; a 405 must keep its Allow."""
        resp = app.post("/health")
        assert resp.status_code == 405
        assert "GET" in resp.headers["allow"]
        assert "ietf-restconf:errors" in resp.json()
