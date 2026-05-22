# GNPy example data

These files are copied from the **pip-installed** `gnpy` package (`gnpy/example-data/`) so the digital twin can run with full GNPy propagation (QoT) without cloning `oopt-gnpy`.

- **CORONET_CONUS_Topology.json** — 75-node US backbone topology
- **eqpt_config.json** — EDFA types, transceiver specs, fiber parameters

To refresh from your venv:

```bash
GNPY_DATA=$(python3 -c "import gnpy, os; print(os.path.join(os.path.dirname(gnpy.__file__), 'example-data'))")
cp "$GNPY_DATA/CORONET_CONUS_Topology.json" "$GNPY_DATA/eqpt_config.json" examples/gnpy-data/
```
