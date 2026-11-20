import { randomBytes } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";
import { TWINS, UI_ORIGIN, UI_PORT, type TwinName } from "./ports";
import type { E2EOptions } from "./fixtures";

// Browser tests against real twins: `npm run test:e2e`. They are kept out of
// vitest, the quality gates and CI (see e2e/README.md).

const here = path.dirname(fileURLToPath(import.meta.url));
const uiDir = path.resolve(here, "..");

// One throwaway token per run. Playwright evaluates this file again in each
// worker, and workers inherit the runner's environment, so setting it here
// once gives the twin, the fixtures and every worker the same token.
process.env.TWINLIGHT_E2E_TOKEN ??= `e2e-${randomBytes(24).toString("base64url")}`;
const token = process.env.TWINLIGHT_E2E_TOKEN;

function twinServer(name: TwinName) {
  const twin = TWINS[name];
  return {
    name: `twin:${name}`,
    command: [
      "bash e2e/twin/start.sh",
      `e2e/twin/${twin.config}`,
      String(twin.restPort),
      String(twin.grpcPort),
      `e2e/.twin-state/${name}`,
    ].join(" "),
    cwd: uiDir,
    url: `${twin.url}/health`,
    // Fail rather than reuse: a twin someone left running holds unknown state.
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "ignore" as const,
    stderr: "pipe" as const,
    env: {
      TWINLIGHT_CORS_ORIGINS: UI_ORIGIN,
      // Empty means "no tokens": the --env-file the twin gets is empty too,
      // so a token exported in the developer's shell cannot leak in.
      TWINLIGHT_API_TOKENS: twin.auth ? `e2e:${token}` : "",
    },
  };
}

export default defineConfig<E2EOptions>({
  testDir: ".",
  testMatch: "**/*.spec.ts",
  outputDir: "test-results",
  // The twins are shared: tests reset them through the API, one at a time.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  use: {
    baseURL: UI_ORIGIN,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium-open",
      use: { ...devices["Desktop Chrome"], apiToken: undefined },
      grepInvert: /@auth/,
    },
    {
      name: "chromium-token",
      use: { ...devices["Desktop Chrome"], apiToken: token },
      // The CORONET twin runs open; the map checks run in the project above.
      grepInvert: /@coronet/,
    },
  ],
  webServer: [
    twinServer("mesh"),
    twinServer("meshAuth"),
    twinServer("coronet"),
    {
      name: "ui",
      command: `npm run dev -- --host 127.0.0.1 --port ${UI_PORT} --strictPort`,
      cwd: uiDir,
      url: UI_ORIGIN,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
