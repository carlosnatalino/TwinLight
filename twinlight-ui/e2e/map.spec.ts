import { test, expect } from "./fixtures";

// Map checks on CORONET CONUS, whose 75 ROADMs carry US coordinates. Tagged
// @coronet: the CORONET twin runs open, so only chromium-open runs these.

test.describe("CORONET map @coronet", () => {
  test.use({ twinName: "coronet" });

  test("the topology opens on the geographic layout", async ({ page, twin }) => {
    const { nodes, links } = await twin.topologyCounts();
    expect(nodes).toBeGreaterThanOrEqual(75);

    await page.goto("/topology");
    await expect(page.getByText(`${links} links, ${nodes} nodes`)).toBeVisible();
    await expect(page.getByLabel("Layout:")).toHaveValue("geographic");
    // The twin's positions were read: no warning in place of the map.
    await expect(page.getByText("node positions could not be read")).toBeHidden();
  });

  test("the graph is drawn on a canvas", async ({ page }) => {
    await page.goto("/topology");
    await expect(page.getByLabel("Layout:")).toHaveValue("geographic");
    // Cytoscape draws into canvases; a drawn map has a non-empty one.
    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible();
    const box = await canvas.boundingBox();
    expect(box?.width).toBeGreaterThan(100);
    expect(box?.height).toBeGreaterThan(100);
  });
});
