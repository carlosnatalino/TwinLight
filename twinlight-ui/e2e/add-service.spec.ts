import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";
import { serviceName } from "./twin";

// The Add Service form, checked against what the twin then holds.

function endPicker(page: Page, end: "A-End" | "Z-End") {
  return page.getByRole("group", { name: end }).getByLabel("Service Interface Point");
}

test.describe("add service form", () => {
  test("creates a service the twin then holds", async ({ page, twin }) => {
    const [a, z] = await Promise.all([twin.sipUuid("trx A"), twin.sipUuid("trx C")]);

    await page.goto("/services/new");
    await page.getByLabel("Service Name").fill("e2e-form-a-to-c");
    await endPicker(page, "A-End").selectOption(a);
    await endPicker(page, "Z-End").selectOption(z);
    await page.getByLabel("Lifecycle State").selectOption("INSTALLED");
    await page.getByRole("button", { name: "Create Service" }).click();

    await expect(page.getByRole("heading", { name: "Service Created" })).toBeVisible();
    const services = await twin.services();
    expect(services).toHaveLength(1);
    const [created] = services;
    expect(serviceName(created)).toBe("e2e-form-a-to-c");
    expect(created["lifecycle-state"]).toBe("INSTALLED");
    expect(created["administrative-state"]).toBe("UNLOCKED");
    // The page shows the UUID the twin assigned.
    await expect(page.getByText(created.uuid, { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "View Services" }).click();
    await expect(page).toHaveURL(/\/services$/);
    await expect(page.getByRole("button", { name: /e2e-form-a-to-c/ })).toBeVisible();
  });

  test("Create Another clears the form", async ({ page, twin }) => {
    const [a, z] = await Promise.all([twin.sipUuid("trx B"), twin.sipUuid("trx A")]);

    await page.goto("/services/new");
    await page.getByLabel("Service Name").fill("e2e-first");
    await endPicker(page, "A-End").selectOption(a);
    await endPicker(page, "Z-End").selectOption(z);
    await page.getByRole("button", { name: "Create Service" }).click();
    await page.getByRole("button", { name: "Create Another" }).click();

    await expect(page.getByLabel("Service Name")).toHaveValue("");
    await expect(endPicker(page, "A-End")).toHaveValue("");
    await expect(endPicker(page, "Z-End")).toHaveValue("");
    await expect(page.getByRole("button", { name: "Create Service" })).toBeDisabled();
  });

  test("the same SIP at both ends cannot be submitted", async ({ page, twin }) => {
    const a = await twin.sipUuid("trx A");

    await page.goto("/services/new");
    const create = page.getByRole("button", { name: "Create Service" });
    await expect(create).toBeDisabled();
    await expect(page.getByText("Select both end-points")).toBeVisible();

    await endPicker(page, "A-End").selectOption(a);
    await endPicker(page, "Z-End").selectOption(a);
    await expect(page.getByText("Must differ from Z-End")).toBeVisible();
    await expect(page.getByText("Must differ from A-End")).toBeVisible();
    await expect(create).toBeDisabled();
    expect(await twin.services()).toHaveLength(0);
  });

  test("shows the twin's refusal and creates nothing", async ({ page, twin }) => {
    const [a, z] = await Promise.all([twin.sipUuid("trx A"), twin.sipUuid("trx C")]);

    await page.goto("/services/new");
    await endPicker(page, "A-End").selectOption(a);
    await endPicker(page, "Z-End").selectOption(z);
    // Passes the form's own checks, but lies far outside the C-band grid.
    await page.getByLabel("Central Frequency (THz)").fill("200");
    await page.getByRole("button", { name: "Create Service" }).click();

    await expect(page.getByText("Failed to create service")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Service Created" })).toBeHidden();
    expect(await twin.services()).toHaveLength(0);
  });
});
