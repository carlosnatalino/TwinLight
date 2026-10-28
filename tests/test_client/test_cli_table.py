"""Tests for the twinlight-client OPM table rows."""

from twinlight.api.internal import _link_failed_measurements, _mock_measurements
from twinlight_client.cli import _METRICS, _header_line, _value_line


def test_every_column_is_a_metric_the_twin_reports():
    reported = set(_mock_measurements("svc-1", 0.0))
    assert {key for key, _, _ in _METRICS} <= reported


def test_row_aligns_under_the_header():
    row = _value_line(1, "12:00:00", _mock_measurements("svc-1", 0.0))
    assert len(row) == len(_header_line())


def test_link_failed_sample_prints_dashes_not_a_crash():
    """None marks what a failed link cannot measure; it must not hit a format spec."""
    row = _value_line(1, "12:00:00", _link_failed_measurements())
    assert "-" in row.split()
    assert "1.00e+00" in row  # the BERs still report their worst case
