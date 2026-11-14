"""OPM samples ↔ TAPI ``MeasurementDetails`` records (TR-548 §6.3.5).

One record per OAM job per tick: a gNMI ``SubscribeResponse`` whose single
update has the oam-job as its path and a serialized ``tapi.MeasurementDetails``
in ``val.proto_bytes``. The details carry every OPM measure of the service as
one ``qualified-measurement`` list entry, sharing the sampled time, resource,
direction and interval stated once at the top (TR-548 §6.1.3: "multiple
measures that share properties ... reported in a block with the shared
property only stated once").

Measure types: the normalized enum where ``NormalizedMeasurementType`` has a
matching member, otherwise the twin's own OPM key as the native type with
explicit units. The YANG says units "should not be stated" where the type
defines them, so normalized measures carry none; their units are the ones
the twin's OPM uses (ps/nm, ps, dimensionless BER).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from twinlight.streaming.proto import gnmi_pb2
from twinlight.streaming.proto import tapi_streaming_performance_pb2 as tapi

# TR-548 §6.3.4: "target: 'tapi' (no formal usage at this point ...)".
GNMI_TARGET = "tapi"


@dataclass(frozen=True)
class MeasureSpec:
    """How one OPM key is reported."""

    opm_key: str
    normalized: int = tapi.NO_NORMALIZED_TYPE
    units: str = ""

    @property
    def native(self) -> str:
        """native-measurement-type: mandatory where no normalized type exists."""
        return "" if self.normalized != tapi.NO_NORMALIZED_TYPE else self.opm_key


# Order is the record's list-index order and is part of the record format.
MEASURES: tuple[MeasureSpec, ...] = (
    # Normalized types.
    MeasureSpec("chromatic-dispersion-ps-per-nm", tapi.CHROM_DISP),
    # GNPy's accumulated PMD, i.e. the mean differential group delay.
    MeasureSpec("pmd-ps", tapi.POL_MODE_DISP),
    MeasureSpec("pre-fec-ber", tapi.FEC_PRE_FEC_BER),
    # The bounded-distance decoder model of the configured code (G.709 RS by
    # default) is the post-FEC BER a device with that FEC would report. The
    # threshold ("cliff") model is a planning convention, so it stays native.
    MeasureSpec("post-fec-ber-hd", tapi.FEC_POST_FEC_BER),
    # Native types, with units. "1" is the UCUM unit of a dimensionless ratio.
    MeasureSpec("gsnr-db", units="dB"),
    MeasureSpec("osnr-db", units="dB"),
    MeasureSpec("osnr-01nm-db", units="dB"),
    MeasureSpec("q-factor-db", units="dB"),
    MeasureSpec("gmi-bits-per-symbol", units="bit/symbol"),
    MeasureSpec("ngmi", units="1"),
    MeasureSpec("post-fec-ber-threshold", units="1"),
)

_BY_NORMALIZED = {m.normalized: m for m in MEASURES if not m.native}
_BY_NATIVE = {m.native: m for m in MEASURES if m.native}


def _qualified_value(value: Any, spec: MeasureSpec, missing: bool) -> tapi.QualifiedMeasuredValue:
    """The value, or the qualifier that replaces it.

    MISSING (TR-548 ST-6.3; tapi-gnmi-streaming value-qualifier): the
    measurement should have been provided but was not — value and units are
    omitted. INVALID: a value that cannot be a measurement (not finite); it
    is still stated, as the YANG asks.
    """
    if missing or value is None:
        return tapi.QualifiedMeasuredValue(value_qualifier=tapi.MISSING)
    value = float(value)
    qualifier = tapi.OK if math.isfinite(value) else tapi.INVALID
    return tapi.QualifiedMeasuredValue(
        value=value, value_qualifier=qualifier, units=spec.units
    )


def measurement_details(
    sample: Mapping[str, Any],
    *,
    native_resource_id: str,
    sample_interval_ns: int,
) -> tapi.MeasurementDetails:
    """Encode one OPM sample (an entry of ``GET /internal/opm``).

    A ``link-failed`` sample reports every measure as MISSING: the receiver
    has no signal, so the stand-in numbers the REST view shows for charts
    (BER 1.0) are not measurements. A sample without measurements (the
    sampler could not read the service) is MISSING throughout as well.
    """
    measurements = sample.get("measurements") or {}
    missing = sample.get("status") == "link-failed" or not measurements
    return tapi.MeasurementDetails(
        # TR-548 §6.3.5 example: sampled time in milliseconds since the epoch
        # ("1693240037000 // 2023-08-28 16:27:17.000"), interval in ns.
        time_measurement_was_sampled=round(float(sample["timestamp"]) * 1000),
        native_resource_id=native_resource_id,
        direction_of_measured_signal=tapi.RECEIVE,
        sample_interval=sample_interval_ns,
        qualified_measurement=[
            tapi.QualifiedMeasurement(
                list_index=index,
                normalized_measurement_type=spec.normalized,
                native_measurement_type=spec.native,
                qualified_measured_value=_qualified_value(
                    measurements.get(spec.opm_key), spec, missing
                ),
            )
            for index, spec in enumerate(MEASURES)
        ],
    )


def oam_job_path(job_uuid: str) -> gnmi_pb2.Path:
    """The record's gNMI path: the oam-job (TR-548 §6.3.3, §6.3.5)."""
    return gnmi_pb2.Path(elem=[gnmi_pb2.PathElem(name="oam-job", key={"uuid": job_uuid})])


def record_response(
    job_uuid: str, logged_ns: int, details: tapi.MeasurementDetails
) -> gnmi_pb2.SubscribeResponse:
    """The SubscribeResponse carrying one record (TR-548 §6.3.5)."""
    return gnmi_pb2.SubscribeResponse(
        update=gnmi_pb2.Notification(
            timestamp=logged_ns,
            prefix=gnmi_pb2.Path(target=GNMI_TARGET),
            update=[
                gnmi_pb2.Update(
                    path=oam_job_path(job_uuid),
                    val=gnmi_pb2.TypedValue(proto_bytes=details.SerializeToString()),
                )
            ],
        )
    )


def job_uuid_of(path: gnmi_pb2.Path) -> str | None:
    """The oam-job UUID a record's path names, if it names one."""
    for elem in path.elem:
        if elem.name == "oam-job":
            return elem.key.get("uuid")
    return None


def decode_measurements(details: tapi.MeasurementDetails) -> dict[str, float | None]:
    """Map a record back to OPM keys; None for a MISSING measure.

    Unknown measure types (a newer twin, another provider) are skipped rather
    than guessed at.
    """
    out: dict[str, float | None] = {}
    for qm in details.qualified_measurement:
        spec = (
            _BY_NATIVE.get(qm.native_measurement_type)
            if qm.native_measurement_type
            else _BY_NORMALIZED.get(qm.normalized_measurement_type)
        )
        if spec is None:
            continue
        qv = qm.qualified_measured_value
        out[spec.opm_key] = None if qv.value_qualifier == tapi.MISSING else qv.value
    return out
