# T-API Network Digital Twin


## Thesis / Main Argument
A multi-dimensional digital twin for optical networks that combines GNPy-based long-term QoT estimation with analytical models and other models for real-time optical signal quality fluctuations, generating rich monitoring outputs including optical performance monitoring (OPM) metrics typical of coherent transceivers, text logs, eye diagrams, and constellation diagrams.

## Notes
- **Multi-dimensional digital twin:** combines slow (long-term) and fast (real-time) timescales in one framework
- **Optical Networking Gym:** reference for the processes and algorithms of maintaining an optical network state, heuristics for path computation, provisioning of lightpaths, etc.
- **GNPy simulator:** used for long-term estimation of Quality of Transmission (QoT) across the optical link (the GNPy code is available within "related-projects"). The input network topology and information follows the standard defined in GNPy, with extensions as needed.
- **Real-time fluctuations:** used alongside analytical models to generate real-time fluctuations of optical signal quality, such as the OCATA solution
- **Elastic optical networks:** the digital twin must allow for the paradigm of elastic optical networks, where each request initially has a required data rate that is further translated into spectral resource requirements trough QoT-aware routing, modulation format, and spectrum assignment algorithms
- **Output modalities:**
  - Instantaneous optical performance monitoring (OPM) measurement through T-API streaming (OSNR, GSNR, pre-FEC-BER, post-FEC-BER, Q-factor, DGD, chromatic dispersion, etc.)
  - Text logs (the ambition is to have something that resembles a full optical domain controller)
  - Eye diagrams
  - Constellation diagrams
- **T-API / TAPI interfaces:** implement Transport API control plane interfaces and telemetry streaming interfaces to make the digital twin operable within standards-compliant network management stacks
- Key selling point: realistic, multi-fidelity signal simulation for network planning and monitoring use cases

## Implementation
- Uses Python 3.12
- Uses type hints in the code
- Contains a full test suite based on the existing literature and T-API specification
- Modular and easily extensible for the multi-band or multi-core scenario
- Well documented
- Provides clear citation using the citation key of the work used to derive functions, equations, etc.
- Uses NetworkX for graph and path computation algorithms
- Uses FastAPI as the library that handles the requests
- Uses NumPy to store network state
- Try to use as much as possible the GNPy API for computation of OSNR and GSNR
- Provide a working example
- Implement a T-API client that accompanies the project and allows users to interact with the software

## Installing and running the digital twin

At the time of writing, the best Python version to use this is 3.12 due to 

### Installing dependencies

First, you must install the necessary dependencies on the current Python virtual environment:

```bash
pip install gnpy
```

Then, you can install the current tool:

```bash
pip install -e .
```

### Run the server side (full GNPy: propagation + QoT)

To use **GNPy for propagation** (real OSNR/GSNR, path-dependent OPM), the config must set both `gnpy.topology` and `gnpy.equipment` to valid JSON files. The CORONET example uses data under `examples/gnpy-data/` (copied from the pip-installed `gnpy` package):

```bash
PYTHONPATH=src venv/bin/python -m tapi_twin --config examples/coronet_conus_config.yaml
```

When equipment is configured, path **finding** uses GNPy’s [path computation](https://gnpy.readthedocs.io/) (weighted shortest path by fiber length). Otherwise the twin uses NetworkX k-shortest paths. GNPy **propagates** the path and computes QoT (GSNR, OSNR, CD, PMD, etc.).

### Run the latest snapshot

The command below loads the latest snapshot saved, if any.

```bash
PYTHONPATH=src venv/bin/python -m tapi_twin --config examples/coronet_conus_config.yaml --restore-latest
```

### Save snapshop

To save a snapshot with the current state, run the command (remember to adjust the host and port if needed):

```bash
curl -X POST http://localhost:8080/admin/snapshot
```

### Run with small topology (twin_config) or on another port

```bash
PYTHONPATH=src venv/bin/python -m tapi_twin --config examples/twin_config.yaml
```

### Run with CORONET topology on port 8081 (e.g. for testing without conflicting with another instance)

```bash
PYTHONPATH=src venv/bin/python -m tapi_twin --config examples/coronet_conus_config.yaml --rest-port 8081
```

## Installing and running the web UI

### Install

Within the `tapi-twin-ui`, to install the packages, run:

```bash
rm -rf node_modules # removes the modules in case they were already installed
npm ci   # uses package-lock.json (preferred for repeatable installs), or use `npm install`
npm run build
```

### Run the web UI

Within the main project folder, run:

```bash
npm --prefix tapi-twin-ui run dev
```

### Create three services (one per modulation format) between random node pairs

Requires the backend to be running (e.g. on port 8080, or 8081 for CORONET). For each modulation format (DP-QPSK, DP-16QAM, DP-64QAM), picks random endpoint pairs and retries until a service is created (or up to 100 attempts per format). Service names include a random suffix so you can run the command multiple times:

```bash
python3 -c "
import urllib.request, json, random
from urllib.error import HTTPError
BASE = 'http://localhost:8080'  # use 8081 for CORONET
r = urllib.request.urlopen(BASE + '/data/tapi-common:context/service-interface-point')
sips = json.loads(r.read())['tapi-common:context']['service-interface-point']
if len(sips) < 2:
    raise SystemExit('Need at least 2 SIPs')
formats = ['DP-QPSK', 'DP-16QAM', 'DP-64QAM']
for mod in formats:
    suffix = f'{random.getrandbits(16):04x}'
    name = f'demo-{mod}-{suffix}'
    for attempt in range(300):
        shuffled = list(sips)
        random.shuffle(shuffled)
        a, z = shuffled[0]['uuid'], shuffled[1]['uuid']
        if a == z:
            continue
        body = {'tapi-connectivity:connectivity-service': {'name': [{'value-name': 'service-name', 'value': name}], 'modulation-format': mod, 'end-point': [{'local-id': 'a', 'service-interface-point': {'service-interface-point-uuid': a}}, {'local-id': 'z', 'service-interface-point': {'service-interface-point-uuid': z}}]}}
        req = urllib.request.Request(BASE + '/data/tapi-connectivity:connectivity-context/connectivity-service', data=json.dumps(body).encode(), method='POST', headers={'Content-Type': 'application/json'})
        try:
            resp = urllib.request.urlopen(req)
            out = json.loads(resp.read())['tapi-connectivity:connectivity-service']
            print(resp.status, mod, name, out['uuid'])
            break
        except HTTPError as e:
            if e.code != 409:
                err_body = e.read().decode() if e.fp else ''
                print(e.code, mod, name, 'FAILED:', err_body[:200] if err_body else e.reason)
                break
    else:
        print('Gave up after 100 attempts:', mod, name)
"
```