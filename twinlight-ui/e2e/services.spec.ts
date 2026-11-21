import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";

// The Services page against services created through T-API. The mesh's
// transceivers are trx A, B and C; every pair is routable.

/** The service list's entry for the service called `name`. */
function listEntry(page: Page, name: string) {
  return page.getByRole("button", { name: new RegExp(name) });
}

/** The detail panel on the right. */
function detailPanel(page: Page) {
  return page
    .getByRole("complementary")
    .filter({ has: page.getByRole("heading", { name: "Service Detail" }) });
}

test.describe("services page", () => {
  test("an empty twin lists no services", async ({ page }) => {
    await page.goto("/services");
    await expect(page.getByRole("heading", { name: "Services 0", level: 1 })).toBeVisible();
    await expect(page.getByText("No services yet")).toBeVisible();
    await expect(page.getByText("Create a service to see the logical topology")).toBeVisible();
  });

  test("lists a service created through T-API, with its states", async ({ page, twin }) => {
    await twin.createService({ name: "e2e-a-to-c", from: "trx A", to: "trx C" });

    await page.goto("/services");
    await expect(page.getByRole("heading", { name: "Services 1", level: 1 })).toBeVisible();
    const entry = listEntry(page, "e2e-a-to-c");
    await expect(entry).toContainText("PLANNED");
    await expect(entry).toContainText("UNLOCKED");
  });

  test("Refresh picks up a service created after the page loaded", async ({ page, twin }) => {
    await page.goto("/services");
    await expect(page.getByText("No services yet")).toBeVisible();

    await twin.createService({ name: "e2e-late", from: "trx B", to: "trx A" });
    await page.getByRole("main").getByRole("button", { name: "Refresh" }).click();
    await expect(listEntry(page, "e2e-late")).toBeVisible();
  });

  test("the detail panel shows the service's end-points and route", async ({ page, twin }) => {
    const svc = await twin.createService({ name: "e2e-detail", from: "trx A", to: "trx C" });
    const info = await twin.serviceInfo(svc.uuid);

    await page.goto("/services");
    await listEntry(page, "e2e-detail").click();

    const panel = detailPanel(page);
    await expect(panel.getByText(svc.uuid, { exact: true })).toBeVisible();
    await expect(panel.getByText("Node: trx A")).toBeVisible();
    await expect(panel.getByText("Node: trx C")).toBeVisible();
    // The route the twin computed, as /internal/services reports it.
    expect(info["total-fiber-km"]).toBeDefined();
    await expect(panel.getByText(`${info["total-fiber-km"]!.toFixed(1)} km`).first()).toBeVisible();
    expect(info.hops?.length).toBeGreaterThan(0);
    for (const hop of info.hops ?? []) {
      await expect(panel.getByText(hop.uid, { exact: true })).toBeVisible();
    }

    await panel.getByRole("button", { name: "Close" }).click();
    await expect(panel).toBeHidden();
  });

  test("changing a state in the panel writes it to the twin", async ({ page, twin }) => {
    const svc = await twin.createService({ name: "e2e-lock", from: "trx A", to: "trx B" });

    await page.goto("/services");
    await listEntry(page, "e2e-lock").click();
    const panel = detailPanel(page);

    await panel.getByLabel("Administrative State").selectOption("LOCKED");
    await expect
      .poll(async () => (await twin.service(svc.uuid))["administrative-state"])
      .toBe("LOCKED");
    await expect(listEntry(page, "e2e-lock")).toContainText("LOCKED");

    await panel.getByLabel("Lifecycle State").selectOption("INSTALLED");
    await expect
      .poll(async () => (await twin.service(svc.uuid))["lifecycle-state"])
      .toBe("INSTALLED");
  });

  test("deleting a service from the panel removes it from the twin", async ({ page, twin }) => {
    await twin.createService({ name: "e2e-keep", from: "trx B", to: "trx C" });
    const doomed = await twin.createService({ name: "e2e-delete", from: "trx C", to: "trx A" });

    await page.goto("/services");
    await listEntry(page, "e2e-delete").click();
    const panel = detailPanel(page);

    // Two steps: the first click only asks for confirmation.
    await panel.getByRole("button", { name: "Delete service" }).click();
    await expect(panel.getByText("Delete this service permanently?")).toBeVisible();
    await panel.getByRole("button", { name: "Confirm" }).click();

    await expect(panel).toBeHidden();
    await expect(listEntry(page, "e2e-delete")).toBeHidden();
    await expect(listEntry(page, "e2e-keep")).toBeVisible();
    const left = (await twin.services()).map((s) => s.uuid);
    expect(left).not.toContain(doomed.uuid);
    expect(left).toHaveLength(1);
  });

  test("cancelling the delete keeps the service", async ({ page, twin }) => {
    await twin.createService({ name: "e2e-cancel", from: "trx A", to: "trx C" });

    await page.goto("/services");
    await listEntry(page, "e2e-cancel").click();
    const panel = detailPanel(page);
    await panel.getByRole("button", { name: "Delete service" }).click();
    await panel.getByRole("button", { name: "Cancel" }).click();

    await expect(panel.getByRole("button", { name: "Delete service" })).toBeVisible();
    expect(await twin.services()).toHaveLength(1);
  });

  test("View monitoring opens the monitoring page for the service", async ({ page, twin }) => {
    const svc = await twin.createService({ name: "e2e-monitor", from: "trx A", to: "trx C" });

    await page.goto("/services");
    await listEntry(page, "e2e-monitor").click();
    await detailPanel(page).getByRole("link", { name: "View monitoring" }).click();
    await expect(page).toHaveURL(`/monitoring?service=${svc.uuid}`);
  });
});
