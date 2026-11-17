"""Bearer-token authentication shared by the REST and gNMI interfaces.

Authentication is switched on by the *existence* of tokens. The accepted set
is read once at startup from ``TWINLIGHT_API_TOKENS`` — a real environment
variable, or failing that a ``.env`` file — and handed to a single
:class:`TokenAuthenticator`. Both northbound interfaces consult that same
object: the HTTP middleware reads the ``Authorization`` header, the gRPC
interceptor reads the ``authorization`` metadata entry, and both expect an
RFC 6750 bearer token. A token therefore works on both interfaces or on
neither, and there is one place to revoke it.

With no tokens configured every interface stays open, which is how the twin
has always behaved; the startup log says so.

The value is a comma- or whitespace-separated list. Each entry is a bare
token or ``name:token``; the name is what the logs and ``/internal/whoami``
report, so a deployment can tell its clients apart and revoke one without
touching the others::

    TWINLIGHT_API_TOKENS=ui:Q2hhbmdlTWUtdWktdG9rZW4,onos:b25vcy1jb250cm9sbGVy

Tokens never come from the YAML configuration: scenario files are committed
next to topologies, and a secret in one is a secret in git history.

Only SHA-256 digests of the tokens are kept by the authenticator, and every
comparison runs over the whole set with :func:`hmac.compare_digest`, so the
time a check takes does not depend on which token, if any, matched.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The variable holding the accepted token set, read by the twin.
TOKENS_ENV_VAR = "TWINLIGHT_API_TOKENS"

#: The variable holding the one token a client presents, read by the clients.
CLIENT_TOKEN_ENV_VAR = "TWINLIGHT_API_TOKEN"

#: The dotenv file read when no other is named, relative to the working dir.
DEFAULT_ENV_FILE = Path(".env")

#: Shortest token accepted. ``secrets.token_urlsafe(32)`` gives 43 characters;
#: the floor exists to reject placeholders like ``changeme``, not to grade
#: entropy.
MIN_TOKEN_LENGTH = 16

# RFC 6750 §2.1 b64token: exactly what may follow "Bearer " in a header, so a
# token that passes this check can always be presented.
_B64TOKEN_RE = re.compile(r"[A-Za-z0-9\-._~+/]+=*")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SEPARATOR_RE = re.compile(r"[,\s]+")

GENERATE_HINT = (
    "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
)


class ApiToken(BaseModel):
    """One accepted token and the principal name it authenticates as."""

    model_config = ConfigDict(frozen=True)

    name: str
    secret: SecretStr


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _fingerprint(token: str) -> str:
    """A short, stable label for an unnamed token, safe to log."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def parse_tokens(raw: str) -> tuple[ApiToken, ...]:
    """Parse a ``TWINLIGHT_API_TOKENS`` value into named tokens.

    Raises ``ValueError`` describing every bad entry by position or name —
    never by its secret, since the message ends up in a terminal or a log.
    """
    entries = [e for e in _SEPARATOR_RE.split(raw.strip()) if e]
    tokens: list[ApiToken] = []
    errors: list[str] = []
    seen_names: dict[str, int] = {}
    seen_secrets: dict[str, int] = {}

    for position, entry in enumerate(entries, start=1):
        name, sep, secret = entry.partition(":")
        if not sep:
            name, secret = "", entry
        label = f"entry {position}" + (f" ({name!r})" if name else "")

        if sep and not _NAME_RE.fullmatch(name):
            errors.append(
                f"entry {position}: a token name is 1-64 characters of "
                "letters, digits, '.', '_' or '-'"
            )
            continue
        if not _B64TOKEN_RE.fullmatch(secret):
            errors.append(
                f"{label}: a token may contain only letters, digits and "
                "'-._~+/' (with optional trailing '='), per RFC 6750 §2.1"
            )
            continue
        if len(secret) < MIN_TOKEN_LENGTH:
            errors.append(
                f"{label}: a token must be at least {MIN_TOKEN_LENGTH} characters"
            )
            continue
        if secret in seen_secrets:
            errors.append(
                f"{label}: same token as entry {seen_secrets[secret]}"
            )
            continue

        name = name or f"token-{_fingerprint(secret)}"
        if name in seen_names:
            errors.append(f"{label}: name already used by entry {seen_names[name]}")
            continue

        seen_secrets[secret] = position
        seen_names[name] = position
        tokens.append(ApiToken(name=name, secret=SecretStr(secret)))

    if errors:
        raise ValueError(
            f"Invalid {TOKENS_ENV_VAR}:\n  " + "\n  ".join(errors)
            + f"\n{GENERATE_HINT}"
        )
    return tuple(tokens)


class _TokenSettings(BaseSettings):
    """Environment-and-dotenv source of the token set.

    pydantic-settings gives the usual precedence — a variable set in the
    process environment wins over the same name in the dotenv file — and
    ``env_ignore_empty`` makes an empty variable (compose's ``${VAR:-}``, an
    ``export VAR=`` left in a shell profile) fall through to the file instead
    of silently switching authentication off.
    """

    model_config = SettingsConfigDict(
        env_prefix="TWINLIGHT_",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    api_tokens: SecretStr | None = None
    api_token: SecretStr | None = None


def _settings(env_file: Path | None) -> _TokenSettings:
    # DEFAULT_ENV_FILE is looked up per call, not bound as a default
    # argument, so the test suite can point it away from a developer's own.
    return _TokenSettings(_env_file=env_file or DEFAULT_ENV_FILE)


def load_tokens(env_file: Path | None = None) -> tuple[ApiToken, ...]:
    """Read the accepted token set from the environment and ``env_file``.

    ``env_file`` defaults to ``./.env``. A missing file is not an error — the
    default one is optional — so a caller that names a file explicitly should
    check it exists first.
    """
    settings = _settings(env_file)
    if settings.api_tokens is None:
        return ()
    return parse_tokens(settings.api_tokens.get_secret_value())


def load_client_token(env_file: Path | None = None) -> str | None:
    """The token a client should present: ``TWINLIGHT_API_TOKEN``, else None.

    Read from the environment, then ``env_file`` (default ``./.env``).
    """
    settings = _settings(env_file)
    if settings.api_token is None:
        return None
    return settings.api_token.get_secret_value().strip() or None


def bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` value.

    The scheme is matched case-insensitively (RFC 7235 §2.1). Any other
    scheme, or a bare ``Bearer``, yields None: no bearer credentials offered.
    """
    if not authorization:
        return None
    scheme, _, credentials = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credentials.strip() or None


class TokenAuthenticator:
    """Checks presented bearer tokens against the configured set.

    Holds only SHA-256 digests. Built once at startup, read-only afterwards,
    and shared by the HTTP middleware and the gRPC interceptor.
    """

    def __init__(self, tokens: Iterable[ApiToken] = ()) -> None:
        self._entries: tuple[tuple[bytes, str], ...] = tuple(
            (_digest(t.secret.get_secret_value()), t.name) for t in tokens
        )

    @property
    def enabled(self) -> bool:
        """True when at least one token is configured, i.e. auth is required."""
        return bool(self._entries)

    @property
    def principals(self) -> tuple[str, ...]:
        """Names of the configured tokens, for logging."""
        return tuple(name for _, name in self._entries)

    def authenticate(self, token: str | None) -> str | None:
        """Return the principal name ``token`` belongs to, or None."""
        if token is None:
            return None
        presented = _digest(token)
        matched: str | None = None
        # No early exit: the loop costs the same whichever entry matches.
        for digest, name in self._entries:
            if hmac.compare_digest(presented, digest):
                matched = name
        return matched

    def __repr__(self) -> str:
        return f"TokenAuthenticator(principals={list(self.principals)!r})"
