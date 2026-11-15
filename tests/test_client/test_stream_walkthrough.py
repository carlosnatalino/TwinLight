"""examples/tapi_stream_walkthrough.py runs end to end against a live twin.

The walkthrough is documentation that executes; this keeps it from rotting.
It runs against a twin with API tokens and a fast sampler, with --fail-link.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from tests.test_streaming.test_gnmi_service import start_twin
from twinlight.auth import parse_tokens
from twinlight.config import TwinConfig

TOKEN = "s3cret-token-0123456789"
SCRIPT = Path(__file__).parents[2] / "examples" / "tapi_stream_walkthrough.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tapi_stream_walkthrough", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve types through it
    spec.loader.exec_module(module)
    return module


async def test_walkthrough_runs_every_step(
    gnpy_twin_config: TwinConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real GNPy, so the service crosses a fiber that step 10 can cut."""
    walkthrough = _load()
    twin_config = gnpy_twin_config
    twin_config.api_tokens = parse_tokens(TOKEN)
    twin_config.streaming.sample_interval_s = 0.05
    twin = await start_twin(twin_config, token=TOKEN)
    stop = asyncio.Event()
    sampler = asyncio.create_task(twin.stream.run(stop))
    try:
        await twin.add_service()
        await asyncio.sleep(0.3)  # some history to replay
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=twin.app), base_url="http://twin",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http:
            opts = walkthrough.Options(
                token=TOKEN, gnmi_target=twin.target, history_s=0.2, live=2, fail_link=True
            )
            assert await walkthrough.run(http, opts) == 0
    finally:
        stop.set()
        await sampler
        await twin.close()

    out = capsys.readouterr().out
    for expected in (
        "authenticated as 'token-",                      # 1: whoami
        "RESTCONF root '/restconf'",                     # 2: host-meta
        "PERIODIC · log TRUNCATED",                      # 3: stream type
        "model tapi-oam 2.6.0",                          # 5: capabilities
        "no token        → UNAUTHENTICATED",             # 6: rejections
        "mode=ONCE       → INVALID_ARGUMENT: subscribe.mode",
        "── sync_response",                              # 7: replay, then live
        "normalized CHROM_DISP",                         # 8: decoding
        "native     gsnr-db",
        "none lost, none twice",                         # 9: resume
        "MISSING",                                       # 10: link failure
        "restored",
    ):
        assert expected in out, expected
    assert "replayed 0 record" not in out


def test_without_a_token_it_says_so(
    twin_config: TwinConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    walkthrough = _load()
    twin_config.api_tokens = parse_tokens(TOKEN)

    async def go() -> int:
        twin = await start_twin(twin_config)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=twin.app), base_url="http://twin"
            ) as http:
                return int(await walkthrough.run(http, walkthrough.Options()))
        finally:
            await twin.close()

    assert asyncio.run(go()) == 1
    assert "requires an API token" in capsys.readouterr().out
