import { test as base, expect, type Page } from "@playwright/test";
import { TWINS, UI_ORIGIN } from "./ports";
import { TwinApi } from "./twin";

/** Options a project or a test sets with `use`. */
export interface E2EOptions {
  /**
   * The API token of this project's twin, or undefined for an open twin.
   * Set per project in playwright.config.ts.
   */
  apiToken: string | undefined;
  /** Which topology the test runs against. CORONET is for the map checks. */
  twinName: "mesh" | "coronet";
  /**
   * Enter the token through the UI's dialog before the test starts (when the
   * project has one). The auth tests turn it off to drive the dialog
   * themselves.
   */
  enterToken: boolean;
}

interface E2EFixtures {
  /** Base URL of the twin this test talks to. */
  twinUrl: string;
  /** The twin's REST API, with the project's token. */
  twin: TwinApi;
  /** Runs before every test: the twin starts with no services. */
  cleanTwin: void;
}

/**
 * Enter `token` through the token dialog the UI opens on a twin that needs
 * one. It cannot be put in Playwright's storage state: the UI keeps it in
 * sessionStorage, which storage state does not cover. A later page.goto in
 * the same page keeps it, since sessionStorage lives as long as the tab.
 */
export async function enterTokenThroughDialog(page: Page, token: string): Promise<void> {
  await page.goto("/");
  const dialog = page.getByRole("dialog", { name: "API token required" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("API Token", { exact: true }).fill(token);
  await dialog.getByRole("button", { name: "Use token" }).click();
  await expect(dialog).toBeHidden();
  await expectConnected(page);
}

/** The header shows the twin as reachable, with an accepted token if one is needed. */
export async function expectConnected(page: Page): Promise<void> {
  await expect(page.getByRole("banner").getByText("Connected", { exact: true })).toBeVisible();
}

export const test = base.extend<E2EOptions & E2EFixtures>({
  apiToken: [undefined, { option: true }],
  twinName: ["mesh", { option: true }],
  enterToken: [true, { option: true }],

  twinUrl: async ({ twinName, apiToken }, use) => {
    if (twinName === "coronet") {
      if (apiToken) throw new Error("The CORONET twin runs without tokens; tag the test @coronet");
      await use(TWINS.coronet.url);
    } else {
      await use(apiToken ? TWINS.meshAuth.url : TWINS.mesh.url);
    }
  },

  twin: async ({ playwright, twinUrl, apiToken }, use) => {
    const request = await playwright.request.newContext({
      baseURL: twinUrl,
      extraHTTPHeaders: apiToken ? { Authorization: `Bearer ${apiToken}` } : {},
    });
    await use(new TwinApi(request, twinUrl));
    await request.dispose();
  },

  cleanTwin: [
    async ({ twin }, use) => {
      await twin.deleteAllServices();
      await use();
    },
    { auto: true },
  ],

  page: async ({ page, context, twinUrl, apiToken, enterToken }, use) => {
    // Point the UI at this test's twin the way a deployment does: through
    // the runtime config.js (see public/config.js), served here instead of
    // the development default. On the context, so a second tab gets it too.
    await context.route(`${UI_ORIGIN}/config.js`, (route) =>
      route.fulfill({
        contentType: "application/javascript",
        body: `window.__TWIN_CONFIG__ = { baseUrl: ${JSON.stringify(twinUrl)} };`,
      }),
    );
    if (apiToken && enterToken) await enterTokenThroughDialog(page, apiToken);
    await use(page);
  },
});

export { expect };
