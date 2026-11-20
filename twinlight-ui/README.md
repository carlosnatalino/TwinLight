# TwinLight — Web UI

A React + TypeScript SPA that connects to the [TwinLight](../README.md) REST API
and provides interactive visualization of the optical network topology, device
details, spectrum occupancy, and live monitoring.

The UI is **fully stateless**: all truth lives in the twin. It polls the REST
API and caches topology and time series in `localStorage` (key prefix
`twinlight-ui:`) so a reload does not lose a monitoring session.

The easiest way to run it together with a twin is the Compose stack described in
the [main README](../README.md#quick-start-with-docker-compose) — everything
below is for UI development.

## Quick start

```bash
npm ci             # reproducible install from package-lock.json
npm run dev        # dev server on http://localhost:5173
npm run build      # production build → dist/
npm test           # test suite (vitest)
npm run lint       # eslint
```

Run these from `twinlight-ui/`, or from the repository root with
`npm --prefix twinlight-ui <script>`.

## Connecting to a twin

1. Start the backend: `twinlight --config examples/twin_config.yaml`
2. Open <http://localhost:5173>
3. Go to **Settings** and enter the twin's base URL (default
   `http://localhost:8080`)
4. If the twin has authentication on (`TWINLIGHT_API_TOKENS` is set), the UI
   asks for one of its tokens in a dialog the first time a call needs it (see
   below). Dismiss it and the header shows *Token required*; the banner's
   **Enter token** or **Settings → API Token** asks again.
5. Click **Test Connection**, then **Refresh** on any page to load data

The UI reads the twin's RESTCONF root from `/.well-known/host-meta` once per
base URL (RFC 8040 §3.1) and sends its T-API requests under it
(`/restconf/data/…` by default); it does not assume `/restconf`.

### API token

The token is sent as `Authorization: Bearer <token>` on every request.

- **Asked for when needed.** On start-up, and on every connection check, the
  UI calls `GET /health` (public) and then `GET /internal/whoami`. A `401`
  there means the twin needs a token, or rejected the one in use, and the UI
  opens a token dialog over whatever page is shown. A twin with
  authentication off answers `whoami` with `"authentication": "disabled"` and
  never triggers it. Pages wait for that check (and for the token, if one is
  needed) before they load, because finding the twin's RESTCONF root
  (`/.well-known/host-meta`) also needs the token.
- **Checked before it is kept.** The dialog tries the token against
  `/internal/whoami`; a rejected one is reported and nothing is stored. An
  accepted one is kept and the pages that were waiting load, with no refresh.
- **Dismissible.** *Not now* (or Escape) closes the dialog for this twin and
  token: polling does not reopen it. The banner's **Enter token**, Settings,
  or a change (a token that worked being rejected later) asks again.
- **Kept for the session.** The token lives in this browser tab's
  `sessionStorage`: a reload keeps it, closing the tab or browser forgets it.
  It is never written to `localStorage` (a token an earlier build left there
  is moved into the session once and deleted), never read from a file or
  from `config.js` — anything there can be read by whoever can load the UI —
  and never put in a URL or an error message. Where `sessionStorage` is
  blocked, it is kept in memory for the page instead.
- **Settings → API Token** changes it, or clears it with an empty field.
  **Test Connection** checks the token as typed, before it is saved.

### CORS

The twin must allow the UI's origin. Both shipped example configs already do:

```yaml
server:
  cors_origins:
    - "http://localhost:5173"
```

`TWINLIGHT_CORS_ORIGINS` (comma-separated) replaces that list without editing
the file; the Compose stack sets it to the UI's published origin,
`http://localhost:${TWINLIGHT_UI_PORT:-5173}`.

## Docker

The UI ships as a static nginx image. The Digital Twin URL is **not** baked in
at build time — it is injected at container startup from the `TWIN_URL`
environment variable into `config.js`, so one image works against any twin.

```bash
# Standalone
docker build -t twinlight-ui ./twinlight-ui
docker run --rm -p 5173:80 -e TWIN_URL="http://localhost:8080" twinlight-ui

# Or as part of the full stack (twin + ui + Prometheus + Grafana)
docker compose up --build      # UI on http://localhost:5173
```

`TWIN_URL` is the address the **browser** uses, so it must be reachable from
the host (the twin's published port, e.g. `http://localhost:8080`) — not the
compose service name. A value saved in the UI's Settings page (localStorage)
overrides the injected default. Make sure the UI's origin is allowed by the
twin (`cors_origins`, or `TWINLIGHT_CORS_ORIGINS`, which Compose sets).

## Features

- **Dashboard** — network health at a glance: node/link/SIP counts, operational state, topology breakdown
- **Topology** — interactive Cytoscape.js graph, pan and zoom, click-through to details. When the twin
  serves node positions, the default **Geographic** layout draws each node where it is: the page follows
  each topology node's standard `tapi-equipment:device-supports-node` reference to its device and reads that
  equipment's `geolocation` (decimal64 strings, parsed strictly by `src/lib/yangNumbers.ts`; a malformed one
  is shown as an error above the graph, never drawn at a made-up place). The projection is equirectangular,
  scaled by the cosine of the map's central latitude (north up, east right; `src/lib/geo.ts` says why), with
  no map tiles and no requests. Nodes at one position — a transceiver at its ROADM — are set apart on a small
  ring around the first, clockwise from north, and nodes without a position are lined up below the map; both
  rules are deterministic. The force-directed, hierarchical, grid, breadth-first and circle layouts stay
  selectable; without positions (none served, or an older twin) the page starts force-directed as before
- **Device detail** — full node view: NEP table, SIP badges, state indicators, connected links, OPM metrics
- **Link detail** — endpoints with node resolution, span element table, state indicators
- **Services** — service list, logical topology graph (drawn at the end-points' geographic positions by
  default when the twin serves them, as on the Topology page), spectrum allocation, delete with confirmation
- **Add service** — A-end (SOURCE) and Z-end (SINK) SIPs, modulation format or automatic
  selection, optional central frequency and launch power, ROADMs to pass through (in order)
  or avoid, admin and lifecycle state. The request is built in `src/lib/serviceRequest.ts`;
  its JSON is pinned by `src/__tests__/fixtures/add-service-request.*.json`, which the
  backend suite posts to the API (`tests/test_api/test_ui_requests.py`)
- **Monitoring** — time-series OPM dashboard (OSNR, GSNR, BER, Q-factor, CD, PMD) with configurable time ranges, pause/resume, CSV export and min/max/avg statistics
- **Spectrum grid** — per-link slot allocation heat map with hover detail
- **Path computation** — A/Z SIP selection, k-shortest paths, ROADMs to pass through or avoid, QoT estimate
- **Equipment** — inventory list grouped and filterable by element type
- **Settings** — twin URL, API token, connection test, poll interval, cache management

Constellation and eye-diagram components exist as placeholders; the backend
synthesises the data (`/internal/services/{uuid}/constellation` and
`/eye-diagram`) but the rendering is not implemented yet.

## Architecture

- **React 18 + TypeScript** on Vite 6
- **Zustand** stores: connection, topology, monitoring, services, selection
- **localStorage** caching: topology and node positions (5-minute TTL) plus time-series ring buffers (1000 points per metric per service)
- **Cytoscape.js** for graph rendering, through `src/components/graph/CytoscapeGraph.tsx`
- **Recharts** for time-series charts
- **Tailwind CSS v3** for styling

### Conventions worth knowing before editing

- **Tailwind stays on v3.** Do not upgrade to v4.
- **npm only** — not pnpm, not bun. Commit `package-lock.json` changes.
- `vite.config.ts` and `vitest.config.ts` are deliberately separate files; the
  `vite` and `vitest/config` type exports conflict when merged.
- Use `globalThis`, not `global`, in tests — the browser tsconfig has no Node
  types.
- Cytoscape stylesheets are typed
  `Array<cytoscape.StylesheetStyle | cytoscape.StylesheetCSS>`, not
  `cytoscape.Stylesheet[]`.
- Local type declarations for untyped packages live in `src/types/`.
- Mount a Cytoscape graph with `CytoscapeGraph`, never with `cytoscape()` in a
  page: it owns the core, runs the layouts and stops one before the next, and
  on unmount destroys the core one animation frame after stopping the last
  layout. An animated `cose` layout runs a final frame after `stop()`, and on
  a core already destroyed that frame throws
  `TypeError: Cannot read properties of null (reading 'notify')`.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Framework | React 18 + TypeScript 5 |
| Build | Vite 6 |
| Routing | React Router v7 |
| State | Zustand v5 |
| Graph | Cytoscape.js |
| Charts | Recharts |
| Styling | Tailwind CSS v3 |
| Testing | Vitest + React Testing Library |
