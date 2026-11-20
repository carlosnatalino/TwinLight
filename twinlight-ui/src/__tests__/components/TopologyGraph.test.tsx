import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render } from "@testing-library/react";
import type { Core, ElementDefinition } from "cytoscape";
import TopologyGraph from "@/components/topology/TopologyGraph";
import type { LayoutName } from "@/lib/cytoscape-layout";
import { collectUncaughtErrors, installCytoscapeDom, wait } from "../helpers/cytoscapeDom";

const ELEMENTS: ElementDefinition[] = [
  { data: { id: "a", label: "a", topoId: "t" } },
  { data: { id: "b", label: "b", topoId: "t" } },
  { data: { id: "c", label: "c", topoId: "t" } },
  { data: { id: "ab", source: "a", target: "b", label: "ab", topoId: "t" } },
  { data: { id: "bc", source: "b", target: "c", label: "bc", topoId: "t" } },
];

let uninstall: () => void;
beforeAll(() => {
  uninstall = installCytoscapeDom();
});
afterAll(() => uninstall());

function renderGraph(cyRef: { current: Core | null }) {
  const graph = (layoutName: LayoutName) => (
    <TopologyGraph
      elements={ELEMENTS}
      layoutName={layoutName}
      showLabels
      showArrows
      onNodeClick={() => {}}
      onLinkClick={() => {}}
      onNodeDblClick={() => {}}
      cyRef={cyRef}
    />
  );
  const view = render(graph("cose"));
  return { view, switchTo: (name: LayoutName) => view.rerender(graph(name)) };
}

describe("TopologyGraph teardown", () => {
  it("leaving the page while the layout runs leaves no page error", async () => {
    const watch = collectUncaughtErrors();
    const cyRef: { current: Core | null } = { current: null };
    const { view } = renderGraph(cyRef);
    const core = cyRef.current as Core;
    expect(core === null).toBe(false);

    await wait(30);
    view.unmount();
    // A boolean: on failure, printing a core would take the worker down.
    const refCleared = cyRef.current === null;
    await wait(300);
    watch.stop();

    expect(watch.errors).toEqual([]);
    expect(core.destroyed()).toBe(true);
    expect(refCleared).toBe(true);
  });

  it("switching the layout and leaving at once leaves no page error", async () => {
    const watch = collectUncaughtErrors();
    const cyRef: { current: Core | null } = { current: null };
    const { view, switchTo } = renderGraph(cyRef);
    await wait(30);
    switchTo("circle");
    view.unmount();
    await wait(300);
    watch.stop();

    expect(watch.errors).toEqual([]);
  });
});
