import { test, expect, enterTokenThroughDialog, expectConnected } from "./fixtures";

// The token dialog against a twin started with TWINLIGHT_API_TOKENS. Tagged
// @auth, so only the chromium-token project runs these.

const TOKEN_KEY = "twinlight-ui:api-token";

test.describe("API token @auth", () => {
  // These tests drive the dialog themselves.
  test.use({ enterToken: false });

  test("the UI asks for a token before loading any page", async ({ page }) => {
    await page.goto("/services");
    await expect(page.getByRole("dialog", { name: "API token required" })).toBeVisible();
    await expect(page.getByRole("banner").getByText("Token required")).toBeVisible();
    await expect(page.getByText("Waiting for an API token…")).toBeVisible();
  });

  test("a rejected token is reported, not echoed and not kept", async ({ page }) => {
    const wrong = "not-the-twin-token-0123456789";
    await page.goto("/");
    const dialog = page.getByRole("dialog", { name: "API token required" });
    await dialog.getByLabel("API Token", { exact: true }).fill(wrong);
    await dialog.getByRole("button", { name: "Use token" }).click();

    const alert = dialog.getByRole("alert");
    await expect(alert).toHaveText("The DT rejected this token. Check it and try again.");
    await expect(dialog).toBeVisible();
    await expect(page.getByText(wrong)).toHaveCount(0);
    expect(await page.evaluate((k) => sessionStorage.getItem(k), TOKEN_KEY)).toBeNull();
  });

  test("the token can be shown while typing", async ({ page }) => {
    await page.goto("/");
    const dialog = page.getByRole("dialog", { name: "API token required" });
    const input = dialog.getByLabel("API Token", { exact: true });
    await expect(input).toHaveAttribute("type", "password");
    await dialog.getByRole("button", { name: "Show token" }).click();
    await expect(input).toHaveAttribute("type", "text");
    await dialog.getByRole("button", { name: "Hide token" }).click();
    await expect(input).toHaveAttribute("type", "password");
  });

  test("an accepted token lasts for the tab, not beyond it", async ({ page, context, apiToken }) => {
    await enterTokenThroughDialog(page, apiToken!);

    // Kept in this tab's sessionStorage, never in localStorage.
    expect(await page.evaluate((k) => sessionStorage.getItem(k), TOKEN_KEY)).toBe(apiToken);
    const local = await page.evaluate(() => JSON.stringify({ ...localStorage }));
    expect(local).not.toContain(apiToken!);

    // A reload in the same tab keeps it.
    await page.reload();
    await expectConnected(page);
    await expect(page.getByRole("dialog", { name: "API token required" })).toBeHidden();

    // A new tab starts without it.
    const other = await context.newPage();
    await other.goto("/");
    await expect(other.getByRole("dialog", { name: "API token required" })).toBeVisible();
  });

  test("Not now leaves a banner that reopens the prompt", async ({ page, apiToken }) => {
    await page.goto("/");
    const dialog = page.getByRole("dialog", { name: "API token required" });
    await dialog.getByRole("button", { name: "Not now" }).click();
    await expect(dialog).toBeHidden();

    await expect(page.getByText(/requires an API token\.$/)).toBeVisible();
    await page.getByRole("button", { name: "Enter token" }).click();
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("API Token", { exact: true }).fill(apiToken!);
    await dialog.getByRole("button", { name: "Use token" }).click();
    await expectConnected(page);
    await expect(page.getByText(/requires an API token\.$/)).toBeHidden();
  });
});
