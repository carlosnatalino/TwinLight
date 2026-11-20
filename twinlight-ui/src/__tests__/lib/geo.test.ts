import { describe, expect, it } from "vitest";
import type { PhysicalContext, TapiNode, Topology } from "@/api/types";
import {
  geographicPositions,
  MAP_SIZE,
  nodePositions,
  parseGeolocation,
  project,
  RING_RADIUS,
  UNPLACED_GAP,
  type GeoPosition,
  type Point,
} from "@/lib/geo";
import {
  availableLayouts,
  chooseLayout,
  LAYOUT_CONFIGS,
  layoutOptions,
} from "@/lib/cytoscape-layout";
import { TapiValueError } from "@/lib/yangNumbers";

// Cities of CORONET CONUS, as its topology file places them.
const SEATTLE = { latitude: 47.60621, longitude: -122.332071 };
const NEW_YORK = { latitude: 40.712784, longitude: -74.005941 };
const MIAMI = { latitude: 25.76168, longitude: -80.19179 };
const SAN_DIEGO = { latitude: 32.715738, longitude: -117.161084 };

function node(uuid: string, device?: string): TapiNode {
  return {
    uuid,
    name: [{ "value-name": "node-name", value: uuid }],
    "layer-protocol-name": ["PHOTONIC_MEDIA"],
    "owned-node-edge-point": [],
    "administrative-state": "UNLOCKED",
    "operational-state": "ENABLED",
    "lifecycle-state": "INSTALLED",
    ...(device
      ? { "tapi-equipment:device-supports-node": { "supporting-device": [{ "device-uuid": device }] } }
      : {}),
  };
}

function topology(...nodes: TapiNode[]): Topology[] {
  return [{ uuid: "t", name: [], "layer-protocol-name": ["PHOTONIC_MEDIA"], node: nodes, link: [] }];
}

function physical(devices: Record<string, unknown>): PhysicalContext {
  return {
    device: Object.entries(devices).map(([uuid, geolocation]) => ({
      uuid,
      name: [],
      equipment: [
        {
          uuid: `${uuid}-eq`,
          name: [],
          "actual-equipment": { "common-equipment-properties": { "equipment-type-name": "Roadm" } },
          ...(geolocation === undefined ? {} : { geolocation }),
        },
      ],
    })) as PhysicalContext["device"],
    "physical-span": [],
  };
}

describe("parseGeolocation", () => {
  it("reads the decimal64 strings the twin sends", () => {
    expect(parseGeolocation({ latitude: "47.60621", longitude: "-122.332071" }, "g")).toEqual({
      latitude: 47.60621,
      longitude: -122.332071,
    });
    expect(parseGeolocation({ latitude: "0.0", longitude: "11.9" }, "g")).toEqual({
      latitude: 0,
      longitude: 11.9,
    });
  });

  it("has nothing to read when there is no geolocation, or only an altitude", () => {
    expect(parseGeolocation(undefined, "g")).toBeUndefined();
    expect(parseGeolocation({ altitude: "12000.0" }, "g")).toBeUndefined();
  });

  it.each([
    [{ latitude: "north", longitude: "1.0" }, "g/latitude"],
    [{ latitude: "1.0", longitude: "" }, "g/longitude"],
    [{ latitude: "1.0" }, "g/longitude"],
    [{ longitude: "1.0" }, "g/latitude"],
    [{ latitude: "1e1", longitude: "1.0" }, "g/latitude"],
    [{ latitude: "90.5", longitude: "1.0" }, "g/latitude"],
    [{ latitude: "1.0", longitude: "-180.0000001" }, "g/longitude"],
    [{ latitude: null, longitude: "1.0" }, "g/latitude"],
    [null, "g"],
    ["47.6,-122.3", "g"],
    [[], "g"],
  ])("refuses %j, naming %s", (value, where) => {
    expect(() => parseGeolocation(value, "g")).toThrow(TapiValueError);
    expect(() => parseGeolocation(value, "g")).toThrow(where);
  });
});

describe("nodePositions", () => {
  it("follows device-supports-node to the device's equipment geolocation", () => {
    const positions = nodePositions(
      topology(node("n-sea", "d-sea"), node("n-mia", "d-mia"), node("n-x", "d-x")),
      physical({
        "d-sea": { latitude: "47.60621", longitude: "-122.332071" },
        "d-mia": { latitude: "25.76168", longitude: "-80.19179" },
        "d-x": undefined, // the topology did not locate it
      }),
    );
    expect(positions).toEqual({ "n-sea": SEATTLE, "n-mia": MIAMI });
  });

  it("is empty for an older twin, which serves neither member", () => {
    expect(nodePositions(topology(node("n-1"), node("n-2")), physical({ "d-1": undefined }))).toEqual(
      {},
    );
    expect(nodePositions(topology(node("n-1", "d-1")), undefined)).toEqual({});
  });

  it("throws for a position that does not parse, naming the device", () => {
    const bad = () =>
      nodePositions(topology(node("n-1", "d-1")), physical({ "d-1": { latitude: 47.6, longitude: "W" } }));
    expect(bad).toThrow(TapiValueError);
    expect(bad).toThrow("device=d-1/equipment=d-1-eq/geolocation/longitude");
  });
});

describe("project", () => {
  const points = project({ sea: SEATTLE, nyc: NEW_YORK, mia: MIAMI, sd: SAN_DIEGO });

  it("draws north up and east right", () => {
    expect(points.sea.x).toBeLessThan(points.nyc.x); // Seattle is west of New York
    expect(points.sea.y).toBeLessThan(points.mia.y); // and north of Miami
    expect(points.sd.x).toBeLessThan(points.mia.x);
    expect(points.mia.y).toBeGreaterThan(points.nyc.y);
  });

  it("fits the map's larger side to MAP_SIZE, centred on the origin", () => {
    const xs = Object.values(points).map((p) => p.x);
    const ys = Object.values(points).map((p) => p.y);
    const width = Math.max(...xs) - Math.min(...xs);
    const height = Math.max(...ys) - Math.min(...ys);
    expect(Math.max(width, height)).toBeCloseTo(MAP_SIZE, 6);
    expect(Math.max(...xs) + Math.min(...xs)).toBeCloseTo(0, 6);
    expect(Math.max(...ys) + Math.min(...ys)).toBeCloseTo(0, 6);
  });

  it("scales longitude by the cosine of the central latitude", () => {
    // 10° of longitude at 60° N is 5° of latitude long: cos 60° = 1/2.
    const p = project({
      a: { latitude: 60, longitude: 10 },
      b: { latitude: 60, longitude: 20 },
      c: { latitude: 65, longitude: 10 },
    });
    // The central latitude is 62.5°, so the ratio is cos 62.5° / 1 per degree.
    const ratio = (p.b.x - p.a.x) / 10 / ((p.a.y - p.c.y) / 5);
    expect(ratio).toBeCloseTo(Math.cos((62.5 * Math.PI) / 180), 9);
  });

  it("draws a network across the antimeridian in one piece", () => {
    const p = project({
      fiji: { latitude: -18.1, longitude: 178.4 },
      samoa: { latitude: -13.8, longitude: -171.8 },
    });
    // Samoa is 10° east of Fiji, not 350° west.
    expect(p.samoa.x).toBeGreaterThan(p.fiji.x);
    expect(p.samoa.x - p.fiji.x).toBeLessThan(MAP_SIZE + 1e-6);
  });

  it("places a single position at the origin", () => {
    expect(project({ only: SEATTLE })).toEqual({ only: { x: 0, y: 0 } });
    expect(project({})).toEqual({});
  });
});

describe("geographicPositions", () => {
  const roadm = (id: string) => ({ id, type: "roadm", label: id });
  const trx = (id: string) => ({ id, type: "transceiver", label: id });
  const geo: Record<string, GeoPosition> = {
    "roadm SEA": SEATTLE,
    "trx SEA": SEATTLE,
    "roadm MIA": MIAMI,
    "trx MIA 1": MIAMI,
    "trx MIA 2": MIAMI,
    "trx MIA 3": MIAMI,
    "roadm NYC": NEW_YORK,
  };
  const nodes = [
    trx("trx SEA"),
    roadm("roadm SEA"),
    trx("trx MIA 3"),
    trx("trx MIA 1"),
    roadm("roadm MIA"),
    trx("trx MIA 2"),
    roadm("roadm NYC"),
  ];
  const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);

  it("keeps the ROADM on its city and sets its transceiver due north of it", () => {
    const out = geographicPositions(nodes, geo)!;
    const projected = project({ SEA: SEATTLE, MIA: MIAMI, NYC: NEW_YORK });
    expect(out["roadm SEA"]).toEqual(projected.SEA);
    expect(out["trx SEA"].x).toBeCloseTo(projected.SEA.x, 9);
    expect(out["trx SEA"].y).toBeCloseTo(projected.SEA.y - RING_RADIUS, 9);
  });

  it("sets several co-located nodes apart on a ring, clockwise from north", () => {
    const out = geographicPositions(nodes, geo)!;
    const centre = out["roadm MIA"];
    const ring = ["trx MIA 1", "trx MIA 2", "trx MIA 3"].map((id) => out[id]);
    for (const p of ring) expect(distance(p, centre)).toBeCloseTo(RING_RADIUS, 9);
    expect(ring[0].y).toBeCloseTo(centre.y - RING_RADIUS, 9); // north
    expect(ring[1].x).toBeGreaterThan(centre.x); // then east
    expect(ring[2].x).toBeLessThan(centre.x); // then west
    // No two nodes share a point.
    const all = Object.values(out).map((p) => `${p.x.toFixed(6)},${p.y.toFixed(6)}`);
    expect(new Set(all).size).toBe(all.length);
  });

  it("is the same whatever order the nodes come in", () => {
    const shuffled = [...nodes].reverse();
    expect(geographicPositions(shuffled, geo)).toEqual(geographicPositions(nodes, geo));
  });

  it("lines nodes without a position up below the map", () => {
    const out = geographicPositions([...nodes, roadm("roadm Nowhere"), trx("trx Nowhere")], geo)!;
    const located = Object.entries(out).filter(([id]) => !id.endsWith("Nowhere"));
    const bottom = Math.max(...located.map(([, p]) => p.y));
    expect(out["roadm Nowhere"].y).toBeCloseTo(bottom + UNPLACED_GAP, 9);
    expect(out["trx Nowhere"].y).toBe(out["roadm Nowhere"].y);
    expect(out["trx Nowhere"].x).toBeGreaterThan(out["roadm Nowhere"].x);
  });

  it("gives nothing when the twin places none of the nodes", () => {
    expect(geographicPositions(nodes, undefined)).toBeUndefined();
    expect(geographicPositions(nodes, {})).toBeUndefined();
    expect(geographicPositions([roadm("elsewhere")], geo)).toBeUndefined();
  });
});

describe("the layout a page draws with", () => {
  it("defaults to Geographic when there are positions, force-directed otherwise", () => {
    expect(chooseLayout(null, true)).toBe("geographic");
    expect(chooseLayout(null, false)).toBe("cose");
  });

  it("keeps the user's choice", () => {
    expect(chooseLayout("dagre", true)).toBe("dagre");
    expect(chooseLayout("circle", false)).toBe("circle");
    expect(chooseLayout("geographic", true)).toBe("geographic");
  });

  it("falls back when Geographic was chosen and the positions went away", () => {
    expect(chooseLayout("geographic", false)).toBe("cose");
  });

  it("offers Geographic only with positions, and every other layout always", () => {
    expect(availableLayouts(true)).toEqual(["geographic", "cose", "dagre", "grid", "breadthfirst", "circle"]);
    expect(availableLayouts(false)).toEqual(["cose", "dagre", "grid", "breadthfirst", "circle"]);
  });

  it("draws Geographic as a fitted preset of the positions, in one step", () => {
    const positions = { a: { x: 1, y: 2 } };
    expect(layoutOptions("geographic", positions)).toEqual({
      name: "preset",
      positions,
      fit: true,
      padding: 40,
      animate: false,
    });
    expect(layoutOptions("geographic", undefined)).toBe(LAYOUT_CONFIGS.cose);
    expect(layoutOptions("grid", positions)).toBe(LAYOUT_CONFIGS.grid);
  });
});
