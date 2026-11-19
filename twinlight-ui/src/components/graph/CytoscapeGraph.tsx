import { useLayoutEffect, useRef, type CSSProperties } from "react";
import cytoscape, {
  type Core,
  type ElementDefinition,
  type LayoutOptions,
  type Layouts,
} from "cytoscape";
import dagre from "cytoscape-dagre";

// Register the dagre layout (safe to call more than once)
try {
  cytoscape.use(dagre);
} catch {
  // already registered
}

export type CytoscapeStylesheet = Array<cytoscape.StylesheetStyle | cytoscape.StylesheetCSS>;

export interface CytoscapeGraphProps {
  elements: ElementDefinition[];
  stylesheet: CytoscapeStylesheet;
  /** Run when the first elements arrive, and again whenever this object changes. */
  layout: LayoutOptions;
  /** Any value: the layout also re-runs whenever it changes. */
  layoutKey?: unknown;
  /** Receives the core once it exists, and `null` before it is torn down. */
  onCy?: (cy: Core | null) => void;
  className?: string;
  style?: CSSProperties;
}

/**
 * A Cytoscape graph that owns its core, the layouts it runs and its teardown.
 *
 * The core must outlive every layout it has run. A `cose` layout with
 * `animate: true` drives its own `requestAnimationFrame` loop, which never
 * checks whether the core has been destroyed, and `stop()` only raises a flag:
 * the frame already scheduled still calls the layout's `done()`, which writes
 * the node positions and fits the viewport. On a destroyed core that write
 * ends in `Core.endBatch()`, which calls `notify` on the renderer that
 * `destroy()` has set to `null`, and the page gets an uncaught `TypeError`
 * (cytoscape 3.33 to 3.35). So a layout is stopped before the next one starts,
 * and on unmount the core leaves the page at once but is destroyed one
 * animation frame later: frame callbacks run in the order they were requested,
 * so the frame the stopped layout had already queued runs first.
 */
export default function CytoscapeGraph({
  elements,
  stylesheet,
  layout,
  layoutKey,
  onCy,
  className,
  style,
}: CytoscapeGraphProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const layoutRef = useRef<Layouts | null>(null);
  const shownRef = useRef(new Map<string, ElementDefinition>());
  const styleRef = useRef(stylesheet);
  const onCyRef = useRef(onCy);
  onCyRef.current = onCy;

  // Layout effects, as in a class component's componentDidMount: the graph is
  // laid out before the browser first paints it.
  //
  // Create the core in a container of its own, so that a core whose destroy
  // is deferred never clears the container of the core that replaces it.
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const container = document.createElement("div");
    container.style.width = "100%";
    container.style.height = "100%";
    host.appendChild(container);

    const cy = cytoscape({ container, style: styleRef.current });
    cyRef.current = cy;
    shownRef.current = new Map();
    onCyRef.current?.(cy);

    return () => {
      onCyRef.current?.(null);
      cyRef.current = null;
      container.remove();
      const last = layoutRef.current;
      layoutRef.current = null;
      if (last) {
        last.stop();
        window.requestAnimationFrame(() => cy.destroy());
      } else {
        cy.destroy();
      }
    };
  }, []);

  useLayoutEffect(() => {
    const cy = cyRef.current;
    if (!cy || styleRef.current === stylesheet) return;
    styleRef.current = stylesheet;
    cy.style(stylesheet);
  }, [stylesheet]);

  // Declared before the layout effect, so a layout always sees the new elements.
  useLayoutEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    syncElements(cy, shownRef.current, elements);
  }, [elements]);

  const hasElements = elements.length > 0;
  useLayoutEffect(() => {
    const cy = cyRef.current;
    if (!cy || !hasElements) return;
    layoutRef.current?.stop();
    const next = cy.layout(layout);
    layoutRef.current = next;
    next.run();
  }, [hasElements, layout, layoutKey]);

  return <div ref={hostRef} className={className} style={style} />;
}

/**
 * Bring the core's elements in line with `next`, matching by id: add the new
 * ones, remove the missing ones, and update the data and classes of the rest
 * only where they changed, so classes set on the core (hidden labels, no
 * arrows) and positions survive an update.
 */
function syncElements(
  cy: Core,
  shown: Map<string, ElementDefinition>,
  next: ElementDefinition[]
) {
  const nextIds = new Set<string>();
  const toAdd: ElementDefinition[] = [];
  cy.batch(() => {
    for (const def of next) {
      const id = String(def.data.id);
      nextIds.add(id);
      const ele = cy.getElementById(id);
      const before = shown.get(id);
      if (
        ele.empty() ||
        (ele.isEdge() &&
          (ele.data("source") !== def.data.source || ele.data("target") !== def.data.target))
      ) {
        if (!ele.empty()) cy.remove(ele);
        toAdd.push(def);
      } else if (before !== def) {
        if (!before || !shallowEqual(before.data, def.data)) ele.data(def.data);
        if (!before || classKey(before.classes) !== classKey(def.classes)) {
          ele.classes(def.classes ?? []);
        }
      }
      shown.set(id, def);
    }
    for (const id of [...shown.keys()]) {
      if (nextIds.has(id)) continue;
      shown.delete(id);
      const ele = cy.getElementById(id);
      if (!ele.empty()) cy.remove(ele);
    }
    // Nodes first, so that every edge finds its ends.
    if (toAdd.length > 0) {
      cy.add([
        ...toAdd.filter((d) => d.data.source == null),
        ...toAdd.filter((d) => d.data.source != null),
      ]);
    }
  });
}

function shallowEqual(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k]);
}

function classKey(classes: ElementDefinition["classes"]): string {
  if (classes == null) return "";
  return (Array.isArray(classes) ? classes : String(classes).split(/\s+/))
    .filter(Boolean)
    .join(" ");
}
