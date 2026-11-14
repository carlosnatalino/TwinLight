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
