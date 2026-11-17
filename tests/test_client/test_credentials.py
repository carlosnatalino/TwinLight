"""How the clients find and present an API token."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import httpx
import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.auth import CLIENT_TOKEN_ENV_VAR, parse_tokens
from twinlight.config import TwinConfig
from twinlight_client import soft_failure
from twinlight_client.client import TapiClient
from twinlight_client.credentials import bearer_headers, resolve_token

UI = "ui-token-0123456789abcdef"


class TestResolveToken:
    def test_explicit_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CLIENT_TOKEN_ENV_VAR, "from-the-environment")
        assert resolve_token(UI) == UI

    def test_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CLIENT_TOKEN_ENV_VAR, UI)
        assert resolve_token(None) == UI

    def test_default_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = tmp_path / ".env"
        env.write_text(f"{CLIENT_TOKEN_ENV_VAR}={UI}\n")
        monkeypatch.setattr("twinlight.auth.DEFAULT_ENV_FILE", env)
        assert resolve_token(None) == UI

    def test_none(self) -> None:
        assert resolve_token(None) is None


class TestHeaders:
    def test_bearer_headers(self) -> None:
        assert bearer_headers(UI) == {"Authorization": f"Bearer {UI}"}
        assert bearer_headers(None) == {}
        assert bearer_headers("") == {}

    async def test_tapi_client_sends_the_token(self) -> None:
        async with TapiClient(token=UI) as client:
            assert client._client.headers["authorization"] == f"Bearer {UI}"

    async def test_tapi_client_sends_nothing_without_one(self) -> None:
        async with TapiClient() as client:
            assert "authorization" not in client._client.headers


class TestSoftFailureAgainstASecuredTwin:
    """The whole command, with its own httpx.Client, against a token-guarded twin."""

    @pytest.fixture
    def secured_app(self, gnpy_twin_config: TwinConfig) -> Any:
        # Real GNPy: /config answers 503 without a loaded backend.
        config = gnpy_twin_config.model_copy(
            update={"api_tokens": parse_tokens(f"ui:{UI}")}
        )
        return create_app(config)

    @pytest.fixture(autouse=True)
    def _in_process(
        self, secured_app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Route the client's own httpx.Client at the in-process twin."""

        def client(**kwargs: Any) -> httpx.Client:
            return TestClient(secured_app, headers=kwargs.get("headers"))

        monkeypatch.setattr(soft_failure.httpx, "Client", client)

    def test_without_a_token_the_401_is_reported(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert soft_failure.main(["params"]) == 2
        err = capsys.readouterr().err
        assert "HTTP 401" in err
        assert "Authorization: Bearer" in err

    def test_with_token_flag(self) -> None:
        assert soft_failure.main(["--token", UI, "params"]) == 0

    def test_with_environment_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CLIENT_TOKEN_ENV_VAR, UI)
        assert soft_failure.main(["params"]) == 0


def test_transparent_client_sends_the_token() -> None:
    from twinlight_client.transparent import TransparentClient

    with TransparentClient("http://twin", token="abc-token-0123456789") as odc:
        assert odc._http.headers["authorization"] == "Bearer abc-token-0123456789"
    with TransparentClient("http://twin") as odc:
        assert "authorization" not in odc._http.headers


def test_transparent_cli_passes_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from twinlight_client import transparent_cli

    seen: dict[str, object] = {}

    class Fake:
        def __init__(self, url: str, client: object = None, token: str | None = None) -> None:
            seen["token"] = token

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(transparent_cli, "TransparentClient", Fake)
    monkeypatch.setattr(transparent_cli, "run_command", lambda args, odc: 0)
    assert transparent_cli.main(["--token", "cli-token-0123456789", "get", "/"]) == 0
    assert seen["token"] == "cli-token-0123456789"
    monkeypatch.setenv("TWINLIGHT_API_TOKEN", "env-token-0123456789")
    assert transparent_cli.main(["get", "/"]) == 0
    assert seen["token"] == "env-token-0123456789"
