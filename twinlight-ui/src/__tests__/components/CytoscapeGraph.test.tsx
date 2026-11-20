import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { StrictMode } from "react";
import { render } from "@testing-library/react";
import type { Core, ElementDefinition } from "cytoscape";
import CytoscapeGraph from "@/components/graph/CytoscapeGraph";
import { CYTOSCAPE_STYLESHEET, LAYOUT_CONFIGS } from "@/lib/cytoscape-layout";
import { collectUncaughtErrors, installCytoscapeDom, wait } from "../helpers/cytoscapeDom";

const node = (id: string): ElementDefinition => ({ data: { id, label: id } });
const edge = (id: string, source: string, target: string): ElementDefinition => ({
  data: { id, source, target, label: id },
});
const ELEMENTS = [node("a"), node("b"), node("c"), edge("ab", "a", "b"), edge("bc", "b", "c")];

let uninstall: () => void;
beforeAll(() => {
  uninstall = installCytoscapeDom();
});
afterAll(() => uninstall());

/** Render a graph and keep hold of its core and of the layout events on it. */
function renderGraph(props: Partial<React.ComponentProps<typeof CytoscapeGraph>> = {}) {
  const seen: { core: Core | null; starts: number; stops: number } = {
    core: null,
    starts: 0,
    stops: 0,
  };
  const onCy = (cy: Core | null) => {
    if (!cy) return;
    seen.core = cy;
    cy.on("layoutstart", () => (seen.starts += 1));
    cy.on("layoutstop", () => (seen.stops += 1));
  };
  const element = (p: typeof props) => (
    <CytoscapeGraph
      elements={ELEMENTS}
      stylesheet={CYTOSCAPE_STYLESHEET}
      layout={LAYOUT_CONFIGS.cose}
      onCy={onCy}
      {...p}
    />
  );
  const view = render(element(props));
  return { seen, view, rerender: (p: typeof props) => view.rerender(element(p)) };
}

describe("CytoscapeGraph", () => {
  it("unmounting while the force-directed layout animates throws nothing", async () => {
    const watch = collectUncaughtErrors();
    const { seen, view } = renderGraph();
    // cose (animate: true) ends when its temperature falls below minTemp,
    // about 35 animation frames in: 50 ms is well inside it.
    await wait(50);
    expect(seen.starts).toBe(1);
    expect(seen.stops).toBe(0);

    view.unmount();
    await wait(300);
    watch.stop();

    expect(watch.errors).toEqual([]);
    expect(seen.core?.destroyed()).toBe(true);
  });

  it("unmounting right after a layout switch throws nothing", async () => {
    const watch = collectUncaughtErrors();
    const { seen, view, rerender } = renderGraph();
    await wait(30);
    rerender({ layout: LAYOUT_CONFIGS.grid });
    expect(seen.starts).toBe(2);
    view.unmount();
    await wait(300);
    watch.stop();

    expect(watch.errors).toEqual([]);
    expect(seen.core?.destroyed()).toBe(true);
  });

  it("leaves one core and throws nothing under StrictMode's mount, unmount, mount", async () => {
    const watch = collectUncaughtErrors();
    const cores: Core[] = [];
    const view = render(
      <StrictMode>
        <CytoscapeGraph
          elements={ELEMENTS}
          stylesheet={CYTOSCAPE_STYLESHEET}
          layout={LAYOUT_CONFIGS.cose}
          onCy={(cy) => cy && cores.push(cy)}
        />
      </StrictMode>
    );
    await wait(300);

    expect(cores).toHaveLength(2);
    expect(cores[0].destroyed()).toBe(true);
    expect(cores[1].destroyed()).toBe(false);
    const host = view.container.firstElementChild as HTMLElement;
    expect(host.children).toHaveLength(1);
    expect(host.querySelectorAll("canvas").length).toBeGreaterThan(0);

    view.unmount();
    await wait(50);
    watch.stop();
    expect(watch.errors).toEqual([]);
    expect(cores[1].destroyed()).toBe(true);
  });

  it("adds, removes and updates elements by id and keeps classes set on the core", () => {
    const { seen, rerender } = renderGraph();
    const cy = seen.core as Core;
    cy.elements().addClass("hidden-label");

    rerender({
      elements: [
        { data: { id: "a", label: "A renamed" } },
        node("b"),
        node("d"),
        edge("ab", "a", "b"),
        edge("bd", "b", "d"),
      ],
    });

    expect(cy.nodes().map((n) => n.id()).sort()).toEqual(["a", "b", "d"]);
    expect(cy.edges().map((e) => e.id()).sort()).toEqual(["ab", "bd"]);
    expect(cy.getElementById("a").data("label")).toBe("A renamed");
    expect(cy.getElementById("a").hasClass("hidden-label")).toBe(true);
    expect(cy.getElementById("ab").hasClass("hidden-label")).toBe(true);
    expect(cy.getElementById("d").hasClass("hidden-label")).toBe(false);
  });

  it("re-runs the layout when the layout or layoutKey changes, and only then", () => {
    const { seen, rerender } = renderGraph({ layoutKey: 1 });
    expect(seen.starts).toBe(1);

    rerender({ layoutKey: 1, elements: [...ELEMENTS] });
    expect(seen.starts).toBe(1);

    rerender({ layoutKey: 2 });
    expect(seen.starts).toBe(2);

    rerender({ layoutKey: 2, layout: LAYOUT_CONFIGS.circle });
    expect(seen.starts).toBe(3);
  });
});
