"""Tests for the twinlight-client OPM table rows and its error messages."""

import threading

import pytest

from twinlight.api.internal import _mock_measurements, _no_signal_measurements
from twinlight_client.cli import _METRICS, _header_line, _value_line


def test_every_column_is_a_metric_the_twin_reports():
    reported = set(_mock_measurements("svc-1", 0.0))
    assert {key for key, _, _ in _METRICS} <= reported


def test_row_aligns_under_the_header():
    row = _value_line(1, "12:00:00", _mock_measurements("svc-1", 0.0))
    assert len(row) == len(_header_line())


@pytest.mark.parametrize("status", ["link-failed", "out-of-band"])
def test_no_signal_record_prints_dashes_not_a_crash(status):
    """A service with no signal streams every measure as MISSING (decoded to None)."""
    from twinlight.streaming.proto import tapi_streaming_performance_pb2 as tapi
    from twinlight.streaming.records import decode_measurements, measurement_details

    details = measurement_details(
        {"service-uuid": "svc-1", "timestamp": 0.0, "status": status,
         "measurements": _no_signal_measurements()},
        native_resource_id="trx", sample_interval_ns=1,
    )
    wire = tapi.MeasurementDetails.FromString(details.SerializeToString())
    row = _value_line(1, "12:00:00", decode_measurements(wire))
    assert row.split()[2:] == ["-"] * len(_METRICS)


def test_rejected_stream_prints_code_and_hint(monkeypatch, capsys):
    """A gRPC rejection is one readable line, with a hint for a missing token."""
    import grpc

    from twinlight_client import cli

    async def fake_fetch(_url, _token=None):
        return [{"uuid": "svc-1", "name": []}]

    async def fake_stream(*_args):
        raise grpc.aio.AioRpcError(
            grpc.StatusCode.UNAUTHENTICATED, grpc.aio.Metadata(), grpc.aio.Metadata(),
            details="missing token",
        )

    monkeypatch.setattr(cli, "_fetch_services", fake_fetch)
    monkeypatch.setattr(cli, "_stream_service", fake_stream)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    assert cli.main(["--rest-url", "http://twin"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == (
        "Streaming error: UNAUTHENTICATED: missing token "
        "(set TWINLIGHT_API_TOKEN (environment or ./.env) or pass --token)"
    )


# -- A stream that cannot be reached ------------------------------------------------
# RESTCONF is faked; the gNMI connection is real, to a port that refuses it.


def _fake_restconf(monkeypatch, advertised: str) -> None:
    from twinlight_client import cli
    from twinlight_client.streaming import StreamEndpoint

    class FakeRest:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

    async def fake_fetch(_url, _token=None):
        return [{"uuid": "svc-1", "name": []}]

    async def fake_discover(_rest):
        return StreamEndpoint("stream-1", advertised, "tapi-streaming:STREAM_STATE_ACTIVE")

    async def fake_job(_rest, _uuid):
        return "job-1"

    monkeypatch.setattr(cli, "_fetch_services", fake_fetch)
    monkeypatch.setattr(cli, "TapiClient", FakeRest)
    monkeypatch.setattr(cli, "discover_stream", fake_discover)
    monkeypatch.setattr(cli, "oam_job_for_service", fake_job)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")


def _main_within(argv: list[str], seconds: float = 10.0) -> int:
    """cli.main(argv), failing the test instead of hanging if it never returns."""
    from twinlight_client import cli

    result: list[int] = []
    worker = threading.Thread(target=lambda: result.append(cli.main(argv)), daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"twinlight-client still running after {seconds} s"
    return result[0]


def test_unreachable_advertised_address_stops_with_both_remedies(
    monkeypatch, capsys, caplog, dead_target
):
    _fake_restconf(monkeypatch, f"grpc://{dead_target}")
    assert _main_within(["--rest-url", "http://twin"]) == 1
    first, second = capsys.readouterr().err.strip().split("\n")
    assert first.startswith(
        f"Cannot reach the measurement stream at {dead_target}, the connection-address "
        f"the twin advertises (grpc://{dead_target}): UNAVAILABLE: "
    )
    assert second == (
        "Pass --gnmi-target HOST:PORT to connect elsewhere, or set "
        "streaming.advertised_address on the twin to an address clients can reach."
    )
    assert "resubscribing" not in caplog.text


def test_unreachable_gnmi_target_names_the_override(monkeypatch, capsys, caplog, dead_target):
    # The advertised address is never tried: --gnmi-target replaces it.
    _fake_restconf(monkeypatch, "grpc://twin.example.org:50051")
    assert _main_within(["--rest-url", "http://twin", "--gnmi-target", dead_target]) == 1
    err = capsys.readouterr().err.strip()
    assert "\n" not in err
    assert err.startswith(
        f"Cannot reach the measurement stream at {dead_target} (from --gnmi-target): "
        "UNAVAILABLE: "
    )
    assert "advertised" not in err
    assert "resubscribing" not in caplog.text
