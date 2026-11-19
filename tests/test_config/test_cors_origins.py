"""``TWINLIGHT_CORS_ORIGINS``: the web UI's origin from the environment.

The variable replaces ``server.cors_origins`` from the YAML, read like the
API tokens (the process environment over the dotenv file, an empty value
counted as unset). Every origin, from either source, must be what a browser
sends as ``Origin`` (RFC 6454 §6.2), or the twin refuses to start.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from twinlight.app import create_app
from twinlight.auth import TOKENS_ENV_VAR
from twinlight.cli import load_config
from twinlight.config import (
    CORS_ORIGINS_ENV_VAR,
    ServerConfig,
    check_origin,
    parse_cors_origins,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
UI = "http://localhost:15176"
YAML_ORIGIN = "http://localhost:5173"


def _config_file(tmp_path: Path, origins: list[str] | None = None) -> Path:
    data: dict = {"gnpy": {"topology": str(FIXTURES / "edfa_example_network.json")},
                  "simulation": {"snapshot_dir": str(tmp_path / "snapshots")}}
    if origins is not None:
        data["server"] = {"cors_origins": origins}
    path = tmp_path / "twin.yaml"
    path.write_text(yaml.dump(data))
    return path


def _load(tmp_path: Path, *extra: str, origins: list[str] | None = None):
    return load_config(["--config", str(_config_file(tmp_path, origins)), "--reset", *extra])


class TestParsing:
    def test_a_comma_separated_list_trimmed_without_empty_entries(self) -> None:
        assert parse_cors_origins(f" {UI} ,, https://twin.example ,") == [
            UI, "https://twin.example"]

    @pytest.mark.parametrize("origin", [
        "http://localhost:5173", "https://twin.example", "http://127.0.0.1:8080",
        "http://[::1]:5173", "http://ui", "app+x.y-z://host", "*",
    ])
    def test_valid_origins(self, origin: str) -> None:
        assert check_origin(origin) == origin

    @pytest.mark.parametrize(("origin", "reason"), [
        ("localhost:5173", "not an origin"),
        ("http://localhost:5173/", "not an origin"),
        ("http://localhost:5173/ui", "not an origin"),
        ("http://localhost:5173?x=1", "not an origin"),
        ("http://user@localhost", "not an origin"),
        ("http://", "not an origin"),
        ("http://localhost:99999", "outside 1-65535"),
        ("http://LocalHost:5173", "'http://localhost:5173'"),
        ("HTTP://localhost", "'http://localhost'"),
        ("http://localhost:80", "'http://localhost'"),
        ("https://twin.example:443", "'https://twin.example'"),
    ])
    def test_invalid_origins_say_why(self, origin: str, reason: str) -> None:
        with pytest.raises(ValueError) as err:
            check_origin(origin)
        assert reason in str(err.value)

    def test_every_invalid_entry_is_reported(self) -> None:
        with pytest.raises(ValueError) as err:
            parse_cors_origins(f"{UI}, localhost:1, http://x/")
        message = str(err.value)
        assert message.startswith(f"Invalid {CORS_ORIGINS_ENV_VAR}:")
        assert "'localhost:1'" in message and "'http://x/'" in message

    @pytest.mark.parametrize("value", [",", " , ,"])
    def test_a_value_naming_no_origin_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="names no origin"):
            parse_cors_origins(value)

    def test_the_config_file_is_checked_the_same_way(self) -> None:
        with pytest.raises(ValidationError, match="not an origin"):
            ServerConfig(cors_origins=["http://localhost:5173/"])
        assert ServerConfig(cors_origins=[UI]).cors_origins == [UI]


class TestPrecedence:
    def test_without_the_variable_the_file_applies(self, tmp_path: Path) -> None:
        config = _load(tmp_path, origins=[YAML_ORIGIN])
        assert config.server.cors_origins == [YAML_ORIGIN]

    def test_without_either_the_default_applies(self, tmp_path: Path) -> None:
        assert _load(tmp_path).server.cors_origins == ["http://localhost:3000"]

    def test_the_variable_replaces_the_files_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(CORS_ORIGINS_ENV_VAR, f"{UI}, http://127.0.0.1:15176")
        config = _load(tmp_path, origins=[YAML_ORIGIN, "http://localhost:3000"])
        assert config.server.cors_origins == [UI, "http://127.0.0.1:15176"]

    def test_an_empty_variable_counts_as_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(CORS_ORIGINS_ENV_VAR, "")
        assert _load(tmp_path, origins=[YAML_ORIGIN]).server.cors_origins == [YAML_ORIGIN]

    def test_the_dotenv_file_is_read_and_the_environment_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / "twin.env"
        env_file.write_text(f"{CORS_ORIGINS_ENV_VAR}=https://from-the-file.example\n")
        config = _load(tmp_path, "--env-file", str(env_file), origins=[YAML_ORIGIN])
        assert config.server.cors_origins == ["https://from-the-file.example"]
        monkeypatch.setenv(CORS_ORIGINS_ENV_VAR, UI)
        config = _load(tmp_path, "--env-file", str(env_file), origins=[YAML_ORIGIN])
        assert config.server.cors_origins == [UI]

    def test_the_default_dotenv_file_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / "default.env"
        env_file.write_text(f"{CORS_ORIGINS_ENV_VAR}={UI}\n")
        monkeypatch.setattr("twinlight.auth.DEFAULT_ENV_FILE", env_file)
        assert _load(tmp_path, origins=[YAML_ORIGIN]).server.cors_origins == [UI]

    @pytest.mark.parametrize("value", ["localhost:5173", "http://localhost:5173/", " , "])
    def test_an_invalid_variable_stops_the_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], value: str,
    ) -> None:
        monkeypatch.setenv(CORS_ORIGINS_ENV_VAR, value)
        with pytest.raises(SystemExit) as exc:
            _load(tmp_path, origins=[YAML_ORIGIN])
        assert exc.value.code == 2
        assert CORS_ORIGINS_ENV_VAR in capsys.readouterr().err

    def test_an_invalid_origin_in_the_file_stops_the_start(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(SystemExit):
            _load(tmp_path, origins=["http://localhost:5173/"])
        assert "not an origin" in capsys.readouterr().err


class TestPreflight:
    """A real CORS preflight against a twin configured from the variable."""

    @pytest.mark.parametrize("tokens", [False, True], ids=["open", "tokens"])
    def test_the_variables_origin_is_allowed_and_the_files_is_not(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tokens: bool,
    ) -> None:
        monkeypatch.setenv(CORS_ORIGINS_ENV_VAR, UI)
        if tokens:
            monkeypatch.setenv(TOKENS_ENV_VAR, "ui:preflight-token-0123456789")
        client = TestClient(create_app(_load(tmp_path, origins=[YAML_ORIGIN])))
        url = "/restconf/data/tapi-common:context"
        headers = {"Access-Control-Request-Method": "GET",
                   "Access-Control-Request-Headers": "authorization"}
        allowed = client.options(url, headers={"Origin": UI, **headers})
        assert allowed.status_code == 200, allowed.text
        assert allowed.headers["access-control-allow-origin"] == UI
        assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
        # The file's origin was replaced, not extended.
        refused = client.options(url, headers={"Origin": YAML_ORIGIN, **headers})
        assert refused.status_code == 400
        assert "access-control-allow-origin" not in refused.headers
        # A simple request from the allowed origin carries the header too, a
        # 401 included when tokens are on.
        resp = client.get(url, headers={"Origin": UI})
        assert resp.status_code == (401 if tokens else 200)
        assert resp.headers["access-control-allow-origin"] == UI
