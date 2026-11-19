"""TapiClient reads the connectivity-service list in its standard body.

RFC 8040 §4.3: a GET of the list answers with the list as the body's one
top-level member, ``tapi-connectivity:connectivity-service``.
"""

from __future__ import annotations

import httpx
import pytest

from tests.test_streaming.test_gnmi_service import start_twin
from twinlight.config import TwinConfig
from twinlight_client.client import TapiClient

_LIST = (
    "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
    "/connectivity-service"
)


def _wired(transport: httpx.AsyncBaseTransport) -> TapiClient:
    rest = TapiClient("http://twin", restconf_root="/restconf")
    rest._client = httpx.AsyncClient(transport=transport, base_url="http://twin")
    return rest


async def test_lists_the_services_of_a_twin(twin_config: TwinConfig) -> None:
    twin = await start_twin(twin_config)
    try:
        uuid = await twin.add_service()
        async with _wired(httpx.ASGITransport(app=twin.app)) as rest:
            services = await rest.get_connectivity_services()
    finally:
        await twin.close()
    assert [s["uuid"] for s in services] == [uuid]
    assert services[0]["end-point"]


async def test_reads_the_list_member() -> None:
    services = [{"uuid": "svc-1"}, {"uuid": "svc-2"}]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"tapi-connectivity:connectivity-service": services})

    async with _wired(httpx.MockTransport(handler)) as rest:
        assert await rest.get_connectivity_services() == services
    assert seen == [_LIST]


async def test_a_body_without_the_list_is_an_error_not_no_services() -> None:
    # The container's envelope, which the list GET answered with before.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "tapi-connectivity:connectivity-context": {
                "connectivity-service": [{"uuid": "svc-1"}]
            }
        })

    async with _wired(httpx.MockTransport(handler)) as rest:
        with pytest.raises(KeyError, match="tapi-connectivity:connectivity-service"):
            await rest.get_connectivity_services()


async def test_reads_the_sips_of_a_twin(twin_config: TwinConfig) -> None:
    """The SIP list and one SIP, in their standard bodies (RFC 8040 §4.3,
    RFC 7951 §5.4): the list, and the entry unwrapped from its array of one."""
    twin = await start_twin(twin_config)
    try:
        async with _wired(httpx.ASGITransport(app=twin.app)) as rest:
            sips = await rest.get_sips()
            one = await rest.get_sip(sips[1]["uuid"])
            jobs = await rest.get_oam_jobs()
    finally:
        await twin.close()
    assert len(sips) == 2 and one == sips[1]
    assert jobs == []


@pytest.mark.parametrize(("method", "body", "error"), [
    # The parent's envelope, which earlier releases answered with.
    ("get_sips", {"tapi-common:context": {"service-interface-point": [{"uuid": "s"}]}}, KeyError),
    ("get_sip", {"tapi-common:context": {"service-interface-point": [{"uuid": "s"}]}}, KeyError),
    ("get_sip", {"tapi-common:service-interface-point": {"uuid": "s"}}, ValueError),
    ("get_sip", {"tapi-common:service-interface-point": []}, ValueError),
    ("get_oam_jobs", {}, KeyError),
    ("get_topologies", {"tapi-topology:topology": []}, KeyError),
])
async def test_an_unexpected_body_is_an_error_not_empty(
    method: str, body: dict, error: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with _wired(httpx.MockTransport(handler)) as rest:
        call = getattr(rest, method)
        with pytest.raises(error):
            await (call("s") if method == "get_sip" else call())
