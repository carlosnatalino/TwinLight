"""The token set: parsing, where it is read from, and how it is checked."""

from __future__ import annotations

from pathlib import Path

import pytest

from twinlight.auth import (
    CLIENT_TOKEN_ENV_VAR,
    MIN_TOKEN_LENGTH,
    TOKENS_ENV_VAR,
    TokenAuthenticator,
    bearer_token,
    load_client_token,
    load_tokens,
    parse_tokens,
)

UI = "ui-token-0123456789abcdef"
ONOS = "onos-token-0123456789abcdef"


class TestParseTokens:
    def test_named_entries(self) -> None:
        tokens = parse_tokens(f"ui:{UI},onos:{ONOS}")
        assert [t.name for t in tokens] == ["ui", "onos"]
        assert [t.secret.get_secret_value() for t in tokens] == [UI, ONOS]

    def test_bare_entry_is_named_by_a_stable_fingerprint(self) -> None:
        (first,) = parse_tokens(UI)
        (again,) = parse_tokens(UI)
        assert first.name.startswith("token-")
        assert first.name == again.name
        assert UI not in first.name

    @pytest.mark.parametrize(
        "raw",
        [
            f"ui:{UI},onos:{ONOS}",
            f"ui:{UI} onos:{ONOS}",
            f"  ui:{UI},\n  onos:{ONOS},\n",
            f"ui:{UI} , , onos:{ONOS}",
        ],
    )
    def test_commas_and_whitespace_both_separate(self, raw: str) -> None:
        assert [t.name for t in parse_tokens(raw)] == ["ui", "onos"]

    def test_empty_value_is_no_tokens(self) -> None:
        assert parse_tokens("") == ()
        assert parse_tokens(" ,\n ") == ()

    def test_base64_padding_is_allowed(self) -> None:
        (token,) = parse_tokens("svc:QUJDREVGR0hJSktMTU5PUA==")
        assert token.secret.get_secret_value().endswith("==")

    def test_secret_is_masked_in_repr(self) -> None:
        (token,) = parse_tokens(f"ui:{UI}")
        assert UI not in repr(token)
        assert UI not in str(token)

    @pytest.mark.parametrize(
        ("raw", "fragment"),
        [
            ("ui:short", f"at least {MIN_TOKEN_LENGTH} characters"),
            ("ui:has\"quote-0123456789", "RFC 6750"),
            ("ui:" + "x" * 8 + "=" + "y" * 8, "RFC 6750"),
            (f"b@d:{UI}", "token name"),
            (f"ui:{UI},ops:{UI}", "same token as entry 1"),
            (f"ui:{UI},ui:{ONOS}", "name already used by entry 1"),
        ],
    )
    def test_rejects(self, raw: str, fragment: str) -> None:
        with pytest.raises(ValueError, match=fragment.replace("(", r"\(")):
            parse_tokens(raw)

    def test_every_error_is_reported_at_once(self) -> None:
        with pytest.raises(ValueError) as exc:
            parse_tokens(f"a:short,b:{UI},c:also-short")
        message = str(exc.value)
        assert "entry 1" in message
        assert "entry 3" in message
        assert "entry 2" not in message

    def test_error_never_echoes_a_secret(self) -> None:
        """The message lands in a terminal or a log; the secret must not."""
        leaked = "almost-a-valid-token!"
        with pytest.raises(ValueError) as exc:
            parse_tokens(f"ui:{leaked},ops:{UI},ops2:{UI}")
        assert leaked not in str(exc.value)
        assert UI not in str(exc.value)

    def test_error_explains_how_to_generate_a_token(self) -> None:
        with pytest.raises(ValueError, match="secrets.token_urlsafe"):
            parse_tokens("ui:short")


class TestLoadTokens:
    def test_nothing_configured_means_no_tokens(self) -> None:
        # conftest clears the environment and points the default .env away.
        assert load_tokens() == ()

    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TOKENS_ENV_VAR, f"ui:{UI}")
        assert [t.name for t in load_tokens()] == ["ui"]

    def test_reads_a_dotenv_file(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(f"# twin tokens\n{TOKENS_ENV_VAR}=ui:{UI},onos:{ONOS}\n")
        assert [t.name for t in load_tokens(env)] == ["ui", "onos"]

    def test_reads_a_multiline_quoted_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(f'{TOKENS_ENV_VAR}="\n  ui:{UI}\n  onos:{ONOS}\n"\n')
        assert [t.name for t in load_tokens(env)] == ["ui", "onos"]

    def test_default_dotenv_file_is_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = tmp_path / "default.env"
        env.write_text(f"{TOKENS_ENV_VAR}=ui:{UI}\n")
        monkeypatch.setattr("twinlight.auth.DEFAULT_ENV_FILE", env)
        assert [t.name for t in load_tokens()] == ["ui"]

    def test_environment_wins_over_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = tmp_path / ".env"
        env.write_text(f"{TOKENS_ENV_VAR}=ui:{UI}\n")
        monkeypatch.setenv(TOKENS_ENV_VAR, f"onos:{ONOS}")
        assert [t.name for t in load_tokens(env)] == ["onos"]

    def test_empty_variable_does_not_switch_auth_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty variable (compose's ``${VAR:-}``) falls through to the file.

        Otherwise one stray empty export would silently open a twin whose
        .env says it requires tokens.
        """
        env = tmp_path / ".env"
        env.write_text(f"{TOKENS_ENV_VAR}=ui:{UI}\n")
        monkeypatch.setenv(TOKENS_ENV_VAR, "")
        assert [t.name for t in load_tokens(env)] == ["ui"]

    def test_unrelated_variables_in_the_file_are_ignored(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(
            f"COMPOSE_PROJECT_NAME=lab\n{CLIENT_TOKEN_ENV_VAR}={UI}\n"
            f"TWINLIGHT_SOMETHING_ELSE=1\n{TOKENS_ENV_VAR}=ui:{UI}\n"
        )
        assert [t.name for t in load_tokens(env)] == ["ui"]

    def test_invalid_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TOKENS_ENV_VAR, "ui:short")
        with pytest.raises(ValueError, match=TOKENS_ENV_VAR):
            load_tokens()


class TestLoadClientToken:
    def test_none_when_unset(self) -> None:
        assert load_client_token() is None

    def test_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CLIENT_TOKEN_ENV_VAR, UI)
        assert load_client_token() == UI

    def test_from_dotenv(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(f"{CLIENT_TOKEN_ENV_VAR}={UI}\n")
        assert load_client_token(env) == UI


class TestBearerToken:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (f"Bearer {UI}", UI),
            (f"bearer {UI}", UI),  # RFC 7235 §2.1: the scheme is case-insensitive
            (f"BEARER {UI}", UI),
            (f"  Bearer   {UI}  ", UI),
            ("Bearer", None),
            ("Bearer   ", None),
            (f"Basic {UI}", None),
            (UI, None),
            ("", None),
            (None, None),
        ],
    )
    def test_extraction(self, header: str | None, expected: str | None) -> None:
        assert bearer_token(header) == expected


class TestTokenAuthenticator:
    def test_disabled_without_tokens(self) -> None:
        auth = TokenAuthenticator()
        assert not auth.enabled
        assert auth.authenticate(UI) is None

    def test_returns_the_matching_principal(self) -> None:
        auth = TokenAuthenticator(parse_tokens(f"ui:{UI},onos:{ONOS}"))
        assert auth.enabled
        assert auth.principals == ("ui", "onos")
        assert auth.authenticate(UI) == "ui"
        assert auth.authenticate(ONOS) == "onos"

    @pytest.mark.parametrize("presented", [None, "", UI[:-1], UI + "x", UI.upper()])
    def test_rejects_anything_else(self, presented: str | None) -> None:
        auth = TokenAuthenticator(parse_tokens(f"ui:{UI}"))
        assert auth.authenticate(presented) is None

    def test_repr_names_principals_not_secrets(self) -> None:
        auth = TokenAuthenticator(parse_tokens(f"ui:{UI}"))
        assert "ui" in repr(auth)
        assert UI not in repr(auth)
