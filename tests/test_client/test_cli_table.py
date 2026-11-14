"""Tests for the twinlight-client OPM table rows."""

from twinlight.api.internal import _link_failed_measurements, _mock_measurements
from twinlight_client.cli import _METRICS, _header_line, _value_line


def test_every_column_is_a_metric_the_twin_reports():
    reported = set(_mock_measurements("svc-1", 0.0))
    assert {key for key, _, _ in _METRICS} <= reported


def test_row_aligns_under_the_header():
    row = _value_line(1, "12:00:00", _mock_measurements("svc-1", 0.0))
    assert len(row) == len(_header_line())


def test_link_failed_record_prints_dashes_not_a_crash():
    """A link-failed service streams every measure as MISSING (decoded to None)."""
    from twinlight.streaming.proto import tapi_streaming_performance_pb2 as tapi
    from twinlight.streaming.records import decode_measurements, measurement_details

    details = measurement_details(
        {"service-uuid": "svc-1", "timestamp": 0.0, "status": "link-failed",
         "measurements": _link_failed_measurements()},
        native_resource_id="trx", sample_interval_ns=1,
    )
    wire = tapi.MeasurementDetails.FromString(details.SerializeToString())
    row = _value_line(1, "12:00:00", decode_measurements(wire))
    assert row.split()[2:] == ["-"] * len(_METRICS)


def test_rejected_stream_prints_code_and_hint(monkeypatch, capsys):
    """A gRPC rejection is one readable line, with a hint for a missing token."""
    import grpc

    from twinlight_client import cli

    async def fake_fetch(_url):
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
        "(set --token or $TWINLIGHT_STREAM_TOKEN)"
    )
