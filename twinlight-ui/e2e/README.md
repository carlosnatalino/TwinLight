# Browser tests

End-to-end tests of the web UI in a real browser (Chromium), against real
twins, with [Playwright Test](https://playwright.dev/docs/intro). They are
separate from the vitest suite and are not part of the quality gates or CI:
run them before changing a page's behaviour, or when a unit test cannot tell
whether the page and the twin agree.

## Running

One-off setup, from `twinlight-ui/`:

```bash
npm ci
npx playwright install chromium
```

The twins need the backend installed in the repository's `.venv`
(`uv venv --python 3.12 && uv pip install -e ".[dev]"` at the repository root),
or set `TWINLIGHT_BIN` to a `twinlight` executable. The GNPy reference files
are fetched on first use if `examples/gnpy-data/` lacks them.

```bash
npm run test:e2e                          # everything, both projects
npm run test:e2e -- services              # files matching "services"
npm run test:e2e -- --project chromium-open
npm run test:e2e:ui                       # Playwright's UI mode
npx playwright show-report e2e/playwright-report
npx playwright show-trace e2e/test-results/<test>/trace.zip
```

One command starts everything and stops it afterwards: Playwright's
`webServer` setting runs the UI (`vite` on 127.0.0.1:15173) and three twins
(`e2e/twin/start.sh`):

| Twin | Topology | Port | Tokens | Used by |
|------|----------|------|--------|---------|
| `mesh` | `tests/fixtures/mesh_network.json` (5 ROADMs, 3 transceivers) | 18080 | off | `chromium-open` |
| `meshAuth` | the same mesh | 18082 | one throwaway token per run | `chromium-token` |
| `coronet` | CORONET CONUS (75 ROADMs) | 18081 | off | tests tagged `@coronet` |

The ports avoid the defaults, so a development stack on 5173/8080 keeps
running. A port already in use fails the run rather than reusing whatever
listens there. Each twin gets an empty `--env-file`, so a `./.env` with tokens
or CORS origins does not leak in; `TWINLIGHT_CORS_ORIGINS` is the test UI's
origin. Nothing is checkpointed: state lives in `e2e/.twin-state/` and every
run starts clean.

A failed test keeps a trace, a screenshot and the page's accessibility
snapshot under `e2e/test-results/`.

## Projects

- **`chromium-open`** runs every test against the open mesh, plus the
  `@coronet` map checks.
- **`chromium-token`** runs every test against the mesh with a token. The
  `page` fixture enters the token through the UI's own dialog before each
  test, because the UI keeps it in `sessionStorage`, which Playwright's saved
  storage state does not cover. The `@auth` tests turn that off
  (`test.use({ enterToken: false })`) and drive the dialog themselves.

## Writing tests

Import `test` and `expect` from `./fixtures`, not from `@playwright/test`. The
fixtures give each test:

- **`page`**, pointed at the test's twin: `config.js` is served with that
  twin's URL, the way a container deployment sets it.
- **`twin`**, the twin's REST API (`twin.ts`) with the project's token: create
  services through T-API as setup, then check the page, or act in the page
  and check the twin.
- **a clean twin**: before every test, all services are deleted with one
  `PUT` of an empty connectivity-context.

Set `test.use({ twinName: "coronet" })` and tag the describe `@coronet` for a
test that needs CORONET.

Locate elements by role and label (`getByRole`, `getByLabel`), never by CSS
class: Tailwind classes change with every restyle. A control a test cannot
find by its label is usually a control a screen reader cannot name either;
fix the markup (`htmlFor`, `aria-labelledby`) rather than reach for a class.

The twins are shared, so tests run one at a time (`workers: 1`). Keep every
test independent of the others' order.
