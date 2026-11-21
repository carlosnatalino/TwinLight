import { test, expect } from "./fixtures";

// The Topology page and the node detail page on the mesh.

test.describe("topology page", () => {
  test("counts the twin's links and nodes", async ({ page, twin }) => {
    const { nodes, links } = await twin.topologyCounts();

    await page.goto("/topology");
    await expect(page.getByText(`${links} links, ${nodes} nodes`)).toBeVisible();
  });

  test("offers every layout and switches between them", async ({ page }) => {
    await page.goto("/topology");
    const layout = page.getByLabel("Layout:");
    // The mesh's ROADMs carry coordinates, so the map comes first.
    await expect(layout).toHaveValue("geographic");
    await expect(layout.getByRole("option")).toHaveText([
      "Geographic",
      "Force-directed",
      "Hierarchical",
      "Grid",
      "Breadth-first",
      "Circle",
    ]);

    await layout.selectOption({ label: "Grid" });
    await expect(layout).toHaveValue("grid");
    await layout.selectOption({ label: "Geographic" });
    await expect(layout).toHaveValue("geographic");
  });

  test("the label and arrow toggles flip", async ({ page }) => {
    await page.goto("/topology");
    await page.getByTitle("Hide labels").click();
    await expect(page.getByTitle("Show labels")).toBeVisible();
    await page.getByTitle("Hide arrows").click();
    await expect(page.getByTitle("Show arrows")).toBeVisible();
  });

  test("a node's detail page shows the node", async ({ page, twin }) => {
    const ref = await twin.nodeRef("roadm B");

    await page.goto(`/topology/${ref.topology}/node/${ref.node}`);
    await expect(page.getByRole("heading", { name: "roadm B", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "State" })).toBeVisible();
  });
});
