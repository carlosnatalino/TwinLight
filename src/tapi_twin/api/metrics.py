"""Prometheus exposition endpoint (``GET /metrics``).

Emits one snapshot per scrape so the values always reflect live state.
Two families of metrics:

* **OPM** — per-service GSNR / OSNR / Q / BER / CD / PMD, computed the
  same way ``/internal/opm`` does (baseline via the active backend +
  the transient layer). One label set per service so Prometheus can
  graph each lightpath independently.
* **/config plane** — per-element parameter values (fiber loss, EDFA
  noise figure, failed-link indicator) plus the runtime-safe twin
  knobs (RMSA margin, transient enable flags). Lets dashboards correlate
  an operator-driven parameter change against the OPM response.

Plus a few `tapi_twin_info{backend=...}` static gauges so dashboards
can show which physical-layer backend produced a series.

This module uses ``prometheus_client`` and builds a *fresh*
``CollectorRegistry`` per scrape — simplest correct pattern when the
underlying data is computed on demand (no need to manage long-lived
metric handles or worry about stale label sets when services come and
go). Cost is one Gauge allocation per metric per scrape, which is
negligible for the topology sizes this twin targets.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge
from prometheus_client.exposition import generate_latest


logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


# --- Per-service OPM gauges --------------------------------------------------
# Service-level metrics carry both ``service_uuid`` (machine key) and
# ``service_name`` (human label) so dashboard panels can show either.
_OPM_LABELS = ("service_uuid", "service_name", "modulation")

_OPM_SPEC: dict[str, tuple[str, str]] = {
    # measurement-key  →  (prometheus metric name suffix, help text)
    "gsnr-db":        ("opm_gsnr_db", "Generalized SNR (signal / (ASE + NLI)) [dB]"),
    "osnr-db":        ("opm_osnr_db", "Optical SNR (ASE only) [dB]"),
    "q-factor-db":    ("opm_q_factor_db", "Q-factor derived from GSNR [dB]"),
    "pre-fec-ber":    ("opm_pre_fec_ber", "Pre-FEC bit-error-ratio"),
    "chromatic-dispersion-ps-per-nm": (
        "opm_chromatic_dispersion_ps_per_nm",
        "Accumulated chromatic dispersion over the path [ps/nm]",
    ),
    "pmd-ps":         ("opm_pmd_ps", "Mean polarization-mode dispersion over the path [ps]"),
}


# --- Per-element /config gauges ---------------------------------------------
# Backend-specific allow-list keys mapped to Prometheus metric names. We
# only export the GNPy/EGN shared subset by default; backend-only attrs
# (e.g. EDFA tilt_target under GNPy) are also exposed when present so
# dashboards under each backend get the full picture.
_FIBER_ATTRS: tuple[tuple[str, str, str], ...] = (
    # (attr, metric_name, help)
    ("loss_coef", "fiber_loss_coef_db_per_km", "Fiber attenuation coefficient [dB/km]"),
    ("att_in",    "fiber_att_in_db", "Fiber connector + padding input attenuation [dB]"),
)
_EDFA_ATTRS: tuple[tuple[str, str, str], ...] = (
    ("nf0",         "edfa_nf_db", "EDFA noise figure [dB]"),
    ("gain_target", "edfa_gain_target_db", "EDFA operational gain target [dB]"),
    ("tilt_target", "edfa_tilt_target_db", "EDFA per-lambda tilt target [dB]"),
    ("out_voa",     "edfa_out_voa_db", "EDFA output VOA setting [dB]"),
)
_ROADM_ATTRS: tuple[tuple[str, str, str], ...] = (
    ("target_pch_out_db",
     "roadm_target_pch_out_dbm",
     "ROADM per-channel target output power [dBm]"),
)


def _make_registry(ctx: Any, cfg: Any) -> CollectorRegistry:
    """Build a fresh registry populated with the current state."""
    registry = CollectorRegistry()
    backend = ctx._backend
    backend_name = backend.name

    # Static info: lets dashboards filter by backend without ambiguity.
    info = Gauge(
        "tapi_twin_info", "Build / backend identity (value is always 1)",
        ["backend"], registry=registry,
    )
    info.labels(backend=backend_name).set(1)

    # Topology counts.
    Gauge(
        "tapi_twin_services_total",
        "Number of admitted connectivity services",
        registry=registry,
    ).set(len(ctx.get_services()))
    Gauge(
        "tapi_twin_failed_links_total",
        "Number of fibers currently flagged failed via /config/set",
        registry=registry,
    ).set(len(ctx.get_failed_links()))

    # Twin-config knobs.
    Gauge(
        "tapi_twin_rmsa_qot_margin_db",
        "QoT admission margin used by RMSA [dB]",
        registry=registry,
    ).set(cfg.rmsa.qot_margin_db)

    g_transient = Gauge(
        "tapi_twin_transient_enabled",
        "Whether a transient impairment model is active (1) or disabled (0)",
        ["model"], registry=registry,
    )
    for model in ("edfa_reservoir", "polarization", "phase_noise", "environmental"):
        model_cfg = getattr(cfg.transients, model)
        g_transient.labels(model=model).set(1 if model_cfg.enabled else 0)

    return registry


def _populate_config_gauges(
    registry: CollectorRegistry, ctx: Any,
) -> None:
    """Per-element parameter gauges + per-fiber failed indicator."""
    backend = ctx._backend
    if not backend.available:
        return
    from tapi_twin.physics.backend import element_kind_name

    failed = ctx.get_failed_links()

    fiber_metrics: dict[str, Gauge] = {}
    edfa_metrics: dict[str, Gauge] = {}
    roadm_metrics: dict[str, Gauge] = {}

    for attr, name, help_text in _FIBER_ATTRS:
        fiber_metrics[attr] = Gauge(
            f"tapi_twin_{name}", help_text, ["uid"], registry=registry,
        )
    for attr, name, help_text in _EDFA_ATTRS:
        edfa_metrics[attr] = Gauge(
            f"tapi_twin_{name}", help_text, ["uid"], registry=registry,
        )
    for attr, name, help_text in _ROADM_ATTRS:
        roadm_metrics[attr] = Gauge(
            f"tapi_twin_{name}", help_text, ["uid"], registry=registry,
        )
    failed_gauge = Gauge(
        "tapi_twin_fiber_failed",
        "1 if the fiber is currently flagged failed, 0 otherwise",
        ["uid"], registry=registry,
    )

    schema = backend.supported_attributes()
    for uid, el in backend.uid_map.items():
        kind = element_kind_name(el)
        attrs_for_kind = schema.get(kind) or {}
        if not attrs_for_kind:
            continue
        if kind == "Fiber":
            failed_gauge.labels(uid=uid).set(1 if uid in failed else 0)
            metrics = fiber_metrics
            attr_table = _FIBER_ATTRS
        elif kind == "Edfa":
            metrics = edfa_metrics
            attr_table = _EDFA_ATTRS
        elif kind == "Roadm":
            metrics = roadm_metrics
            attr_table = _ROADM_ATTRS
        else:
            continue
        for attr, _name, _help in attr_table:
            if attr not in attrs_for_kind:
                continue
            try:
                value = backend.read_attribute(uid, attr)
            except Exception:  # noqa: BLE001
                continue
            if value is None:
                continue
            metrics[attr].labels(uid=uid).set(float(value))


async def _populate_opm_gauges(
    registry: CollectorRegistry, ctx: Any, cfg: Any, t: float,
) -> None:
    """Per-service OPM gauges + link-failed indicator."""
    opm_gauges: dict[str, Gauge] = {
        key: Gauge(
            f"tapi_twin_{name}", help_text, _OPM_LABELS, registry=registry,
        )
        for key, (name, help_text) in _OPM_SPEC.items()
    }
    status_gauge = Gauge(
        "tapi_twin_service_link_failed",
        "1 if the service's path crosses a failed fiber, 0 otherwise",
        ("service_uuid", "service_name"),
        registry=registry,
    )

    for svc in ctx.get_services():
        name_label = next(
            (n.value for n in svc.name if n.value_name == "service-name"),
            svc.uuid[:8],
        )
        modulation = svc.modulation_format.value
        baseline = await ctx.get_or_compute_baseline(svc)
        link_failed = (
            baseline is not None and baseline.status == "link-failed"
        )
        status_gauge.labels(
            service_uuid=svc.uuid, service_name=name_label,
        ).set(1 if link_failed else 0)

        if baseline is None or link_failed:
            # No physics baseline (GNPy/EGN unavailable, or path failed).
            # We deliberately do NOT export sentinel zeros for the metric
            # gauges — Prometheus dashboards render gaps naturally when
            # a series stops emitting samples.
            continue

        from tapi_twin.physics.transients.cascade import apply_all_transients

        try:
            measurements = apply_all_transients(
                baseline, svc.uuid, modulation, t,
                cfg.transients, edfa_tracker=ctx.edfa_tracker,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to compute OPM for service %s: %s", svc.uuid, exc,
            )
            continue
        for key, gauge in opm_gauges.items():
            value = measurements.get(key)
            if value is None:
                continue
            gauge.labels(
                service_uuid=svc.uuid,
                service_name=name_label,
                modulation=modulation,
            ).set(float(value))


@router.get(
    "/metrics",
    response_class=Response,
    include_in_schema=False,
    summary="Prometheus exposition endpoint",
)
async def metrics(request: Request) -> Response:
    """Prometheus text-format snapshot of live config + OPM state.

    Each scrape recomputes the OPM measurements (baseline + transients)
    for every active service, so a Prometheus scrape interval of 15-30
    seconds gives a realistic time-series of how the network is moving
    under the live transient models. Keep the scrape interval at or
    above the slowest transient timescale (~minutes) — sub-second
    polling adds load without showing new physics.
    """
    ctx = request.app.state.context
    cfg = request.app.state.config
    t = time.time()

    registry = _make_registry(ctx, cfg)
    _populate_config_gauges(registry, ctx)
    await _populate_opm_gauges(registry, ctx, cfg, t)

    output = generate_latest(registry)
    return Response(content=output, media_type=CONTENT_TYPE_LATEST)
