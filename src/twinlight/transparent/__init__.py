"""The transparent optical domain controller.

TwinLight's T-API northbound is the *abstracted* view of the network:
service-interface points, nodes, links and connectivity services. An
optical domain controller can also be *fully transparent* — exposing every
physical-layer element and every parameter it models, so that an upper
orchestrator (or an experimenter) can read and set any of them directly,
without going through T-API objects at all.

This package is that transparent view, served under ``/transparent``
(``api/transparent.py``):

* :mod:`.path` — ns-3-style paths (``/ElementList/*/$Fiber/loss_coef``);
* :mod:`.tree` — the data tree those paths walk, built from the live twin;
* :mod:`.datastore` — RESTCONF/NMDA-style datastores (running, candidate,
  startup, operational), validation, commit and the QoT what-if.

Nothing here owns network state: every write goes through ``TapiContext``
(``apply_element_overrides_atomic``, ``apply_twin_overrides`` and the revert
methods), so baselines are invalidated, snapshots capture the change, and
the T-API, OPM, gNMI and Prometheus views stay consistent with it.
"""
