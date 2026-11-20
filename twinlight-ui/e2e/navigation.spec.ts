import { test, expect, expectConnected } from "./fixtures";

test.describe("navigation", () => {
  test("the dashboard loads and shows the twin as connected", async ({ page, twinUrl }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
    await expectConnected(page);
    // The header names the twin the UI talks to: the test's, not the default.
    await expect(page.getByRole("banner").getByText(twinUrl)).toBeVisible();
  });

  test("the sidebar reaches every page", async ({ page }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation");

    const pages: { link: string; path: RegExp; heading: RegExp }[] = [
      { link: "Services", path: /\/services$/, heading: /^Services/ },
      { link: "Add Service", path: /\/services\/new$/, heading: /^Add Connectivity Service$/ },
      { link: "Settings", path: /\/settings$/, heading: /^Settings$/ },
      { link: "Dashboard", path: /\/$/, heading: /^Dashboard$/ },
    ];
    for (const p of pages) {
      await nav.getByRole("link", { name: p.link, exact: true }).click();
      await expect(page).toHaveURL(p.path);
      await expect(page.getByRole("heading", { name: p.heading, level: 1 })).toBeVisible();
    }

    // Pages without a level-1 heading: the URL and the active link.
    for (const link of ["Topology", "Equipment", "Path", "Spectrum", "Spectrum grid", "Monitoring"]) {
      const navLink = nav.getByRole("link", { name: link, exact: true });
      await navLink.click();
      await expect(navLink).toHaveAttribute("aria-current", "page");
    }
  });
});
