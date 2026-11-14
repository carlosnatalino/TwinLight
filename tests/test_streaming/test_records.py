"""OPM samples encoded as TAPI MeasurementDetails, and decoded back."""

from __future__ import annotations

import math

from twinlight.api.internal import _link_failed_measurements, _mock_measurements
from twinlight.streaming.proto import gnmi_pb2
from twinlight.streaming.proto import tapi_streaming_performance_pb2 as tapi
from twinlight.streaming.records import (
    MEASURES,
    decode_measurements,
    job_uuid_of,
    measurement_details,
    record_response,
)

T = 1_700_000_000.25
OPM_KEYS = set(_link_failed_measurements())


def _sample(**overrides: object) -> dict:
    measurements = _mock_measurements("svc", T)
    return {"service-uuid": "svc", "timestamp": T, "measurements": measurements, **overrides}


def _encode(sample: dict) -> tapi.MeasurementDetails:
    return measurement_details(sample, native_resource_id="trx Brest_KLA", sample_interval_ns=10**9)


def test_every_opm_measure_is_reported_exactly_once() -> None:
    """A key added to the OPM without a mapping would silently not stream."""
    keys = [m.opm_key for m in MEASURES]
    assert len(keys) == len(set(keys))
    assert set(keys) == OPM_KEYS


def test_normalized_types_where_the_enum_has_one() -> None:
    by_key = {m.opm_key: m for m in MEASURES}
    assert by_key["chromatic-dispersion-ps-per-nm"].normalized == tapi.CHROM_DISP
    assert by_key["pmd-ps"].normalized == tapi.POL_MODE_DISP
    assert by_key["pre-fec-ber"].normalized == tapi.FEC_PRE_FEC_BER
    assert by_key["post-fec-ber-hd"].normalized == tapi.FEC_POST_FEC_BER


def test_native_types_carry_units_and_normalized_do_not() -> None:
    details = _encode(_sample())
    for qm in details.qualified_measurement:
        units = qm.qualified_measured_value.units
        if qm.native_measurement_type:
            assert qm.normalized_measurement_type == tapi.NO_NORMALIZED_TYPE
            assert units
        else:
            assert qm.normalized_measurement_type != tapi.NO_NORMALIZED_TYPE
            assert units == ""
    native = {qm.native_measurement_type: qm.qualified_measured_value.units
              for qm in details.qualified_measurement if qm.native_measurement_type}
    assert native["gsnr-db"] == native["osnr-db"] == native["q-factor-db"] == "dB"
    assert native["gmi-bits-per-symbol"] == "bit/symbol"
    assert native["ngmi"] == "1"


def test_shared_properties_stated_once() -> None:
    details = _encode(_sample())
    assert details.time_measurement_was_sampled == 1_700_000_000_250  # ms
    assert details.sample_interval == 10**9  # ns
    assert details.native_resource_id == "trx Brest_KLA"
    assert details.direction_of_measured_signal == tapi.RECEIVE
    assert [qm.list_index for qm in details.qualified_measurement] == list(range(len(MEASURES)))
    for qm in details.qualified_measurement:
        assert qm.time_measurement_was_sampled == 0
        assert qm.native_resource_id == ""


def test_round_trip_through_the_wire() -> None:
    sample = _sample()
    wire = _encode(sample).SerializeToString()
    decoded = decode_measurements(tapi.MeasurementDetails.FromString(wire))
    assert decoded == sample["measurements"]


def test_link_failed_is_missing_without_value_or_units() -> None:
    details = _encode({
        "service-uuid": "svc", "timestamp": T, "status": "link-failed",
        "measurements": _link_failed_measurements(),
    })
    for qm in details.qualified_measurement:
        qv = qm.qualified_measured_value
        # Nothing but the qualifier on the wire: no value, no units.
        assert qv.SerializeToString() == tapi.QualifiedMeasuredValue(
            value_qualifier=tapi.MISSING
        ).SerializeToString()
    assert set(decode_measurements(details).values()) == {None}


def test_sample_without_measurements_is_missing() -> None:
    details = _encode({"service-uuid": "svc", "timestamp": T})
    assert {qm.qualified_measured_value.value_qualifier
            for qm in details.qualified_measurement} == {tapi.MISSING}


def test_non_finite_value_is_invalid_but_stated() -> None:
    sample = _sample()
    sample["measurements"]["gsnr-db"] = math.inf
    [qm] = [q for q in _encode(sample).qualified_measurement
            if q.native_measurement_type == "gsnr-db"]
    assert qm.qualified_measured_value.value_qualifier == tapi.INVALID
    assert qm.qualified_measured_value.value == math.inf


def test_record_shape_matches_tr548_example() -> None:
    resp = record_response("job-1", 1_693_240_502_242_882_097, _encode(_sample()))
    assert resp.WhichOneof("response") == "update"
    note = resp.update
    assert note.timestamp == 1_693_240_502_242_882_097
    assert note.prefix.target == "tapi"
    [update] = note.update
    assert update.path == gnmi_pb2.Path(
        elem=[gnmi_pb2.PathElem(name="oam-job", key={"uuid": "job-1"})]
    )
    assert job_uuid_of(update.path) == "job-1"
    assert update.val.WhichOneof("value") == "proto_bytes"
    tapi.MeasurementDetails.FromString(update.val.proto_bytes)


def test_unknown_measure_types_are_skipped() -> None:
    details = tapi.MeasurementDetails(qualified_measurement=[
        tapi.QualifiedMeasurement(native_measurement_type="vendor-x"),
        tapi.QualifiedMeasurement(normalized_measurement_type=tapi.OPTICAL_GAIN),
    ])
    assert decode_measurements(details) == {}
