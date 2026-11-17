"""How the TwinLight clients find and present an API token.

A twin with authentication enabled accepts the tokens in its
``TWINLIGHT_API_TOKENS``; a client presents exactly one, from ``--token`` or
else ``TWINLIGHT_API_TOKEN`` (the environment first, then ``./.env``). The
same token authenticates REST and gNMI.
"""

from __future__ import annotations

import argparse

from twinlight.auth import CLIENT_TOKEN_ENV_VAR, load_client_token


def bearer_headers(token: str | None) -> dict[str, str]:
    """The HTTP headers that present ``token``, or none for no token."""
    return {"Authorization": f"Bearer {token}"} if token else {}


def add_token_argument(parser: argparse.ArgumentParser) -> None:
    """The ``--token`` option shared by the client commands."""
    parser.add_argument(
        "--token",
        default=None,
        help=(
            "API token for a twin with authentication enabled (default: "
            f"${CLIENT_TOKEN_ENV_VAR}, or that variable in ./.env). Prefer "
            "the variable: a command-line argument is visible to other "
            "users in the process list."
        ),
    )


def resolve_token(explicit: str | None) -> str | None:
    """``explicit`` if given, else ``TWINLIGHT_API_TOKEN``, else None."""
    return explicit or load_client_token()
