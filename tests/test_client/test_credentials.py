"""How the clients find and present an API token."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
