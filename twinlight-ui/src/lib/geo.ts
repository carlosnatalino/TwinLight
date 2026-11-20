/**
 * Where to draw a node: its geographic position, read from T-API v2.6.0.
 *
 * The twin serves positions in standard members only:
 *
 *   topology node / "tapi-equipment:device-supports-node" / "supporting-device"
 *     -> physical-context device -> equipment / "geolocation"
 *
 * `geolocation` holds `latitude` and `longitude` in decimal degrees, decimal64
 * leaves and so JSON strings (RFC 7951 §6.1), read here strictly through
 * `lib/yangNumbers.ts`: a malformed or out-of-range value throws
 * `TapiValueError` naming the leaf, it never becomes 0 or NaN. An element the
 * topology does not locate has no `geolocation` at all.
 *
 * The map is drawn with an equirectangular projection scaled by the cosine of
 * the map's central latitude: x = (lon - lon0) cos(lat0), y = -(lat - lat0).
 * North is up and east is right; distances along the central parallel are
 * true, and east-west distances elsewhere are off by cos(lat)/cos(lat0) (about
 * ±20 % at the edges of the contiguous US), far less than a plain
 * latitude-longitude plot, which stretches a 40° N network by 1/cos(40°) =
 * 1.3 horizontally. It is chosen over Mercator because Mercator inflates
 * northern areas without bound and buys nothing a network map needs (it
 * preserves angles, not the distances a span length is read from). The
 * projection is a pure function of the positions: no tiles, no requests.
 */

import type { PhysicalContext, Topology } from "@/api/types";
import { parseDecimal64, TapiValueError } from "@/lib/yangNumbers";

export interface GeoPosition {
  /** Decimal degrees, -90..90. */
  latitude: number;
  /** Decimal degrees, -180..180. */
  longitude: number;
}

export interface Point {
  x: number;
  y: number;
}

/** A node of a drawn graph, as much of it as the layout needs. */
export interface GraphNode {
  id: string;
  /** "roadm" | "transceiver" (lib/tapi-helpers.ts getNodeType). */
  type?: string;
  label?: string;
}

/** The larger side of the drawn map, in model units, before the viewport fit. */
export const MAP_SIZE = 3000;
/** Distance from a shared position to the nodes set around its first node. */
export const RING_RADIUS = 48;
/** Room each node set on a ring needs along it, in model units. */
const RING_SPACING = 56;
/** Gap between the map and the row of nodes the twin gives no position. */
export const UNPLACED_GAP = 160;
/** Spacing of the nodes in that row. */
export const UNPLACED_SPACING = 120;

function describe(value: unknown): string {
  return typeof value === "string" ? JSON.stringify(value) : String(value);
}

/**
 * An equipment's `geolocation`, or undefined when it has none. Throws
 * `TapiValueError` for a container that is not one, a latitude without a
 * longitude (or the reverse), a value that is not a decimal64, or one outside
 * the range the YANG gives the leaf.
 */
export function parseGeolocation(value: unknown, where: string): GeoPosition | undefined {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TapiValueError(`${where}: expected a container, got ${describe(value)}`);
  }
  const { latitude, longitude } = value as Record<string, unknown>;
  // A geolocation with only an altitude places nothing on a map.
  if (latitude === undefined && longitude === undefined) return undefined;
  const lat = parseDecimal64(latitude, `${where}/latitude`);
  const lon = parseDecimal64(longitude, `${where}/longitude`);
  if (lat < -90 || lat > 90) {
    throw new TapiValueError(`${where}/latitude: ${describe(latitude)} is outside -90..90`);
  }
  if (lon < -180 || lon > 180) {
    throw new TapiValueError(`${where}/longitude: ${describe(longitude)} is outside -180..180`);
  }
  return { latitude: lat, longitude: lon };
}

/**
 * Node UUID -> position, for every topology node whose supporting device has
 * an equipment with a `geolocation`. Empty for a twin that serves no
 * positions (an older one has neither member). Throws `TapiValueError` for a
 * position that does not parse.
 */
export function nodePositions(
  topologies: Topology[],
  physical: PhysicalContext | undefined,
): Record<string, GeoPosition> {
  const byDevice = new Map<string, GeoPosition>();
  for (const device of physical?.device ?? []) {
    for (const equipment of device.equipment ?? []) {
      const position = parseGeolocation(
        equipment.geolocation,
        `device=${device.uuid}/equipment=${equipment.uuid}/geolocation`,
      );
      if (position && !byDevice.has(device.uuid)) byDevice.set(device.uuid, position);
    }
  }
  const positions: Record<string, GeoPosition> = {};
  for (const topology of topologies) {
    for (const node of topology.node) {
      const refs = node["tapi-equipment:device-supports-node"]?.["supporting-device"] ?? [];
      for (const ref of refs) {
        const position = byDevice.get(ref["device-uuid"]);
        if (position) {
          positions[node.uuid] = position;
          break;
        }
      }
    }
  }
  return positions;
}

/**
 * Longitudes as one continuous range: a network that straddles the
 * antimeridian (spanning less going the other way round) gets its western
 * longitudes shifted by 360°, so it is drawn in one piece.
 */
function unwrapLongitudes(longitudes: number[]): number[] {
  const span = (values: number[]) => Math.max(...values) - Math.min(...values);
  const shifted = longitudes.map((lon) => (lon < 0 ? lon + 360 : lon));
  return span(shifted) < span(longitudes) ? shifted : longitudes;
}

/**
 * Project positions onto the drawing plane (see the module comment), scaled
 * so that the map's larger side is `MAP_SIZE` and centred on the origin.
 */
export function project(positions: Record<string, GeoPosition>): Record<string, Point> {
  const ids = Object.keys(positions);
  if (ids.length === 0) return {};
  const lats = ids.map((id) => positions[id].latitude);
  const lons = unwrapLongitudes(ids.map((id) => positions[id].longitude));
  const [latMin, latMax] = [Math.min(...lats), Math.max(...lats)];
  const [lonMin, lonMax] = [Math.min(...lons), Math.max(...lons)];
  const lat0 = (latMin + latMax) / 2;
  const lon0 = (lonMin + lonMax) / 2;
  const k = Math.cos((lat0 * Math.PI) / 180);
  const extent = Math.max((lonMax - lonMin) * k, latMax - latMin);
  const scale = extent > 0 ? MAP_SIZE / extent : 1;
  const points: Record<string, Point> = {};
  ids.forEach((id, i) => {
    points[id] = {
      x: (lons[i] - lon0) * k * scale,
      y: (lat0 - lats[i]) * scale,
    };
  });
  return points;
}

function byRoleThenName(a: GraphNode, b: GraphNode): number {
  const rank = (n: GraphNode) => (n.type === "roadm" ? 0 : 1);
  return (
    rank(a) - rank(b) ||
    (a.label ?? "").localeCompare(b.label ?? "") ||
    (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
  );
}

/**
 * Where to draw each node of a graph on the map, or undefined when the twin
 * places none of them (the page then lays the graph out as it always did).
 *
 * Nodes at one position — a transceiver sits at its ROADM's coordinates —
 * are told apart by a fixed offset: the first of them (a ROADM before a
 * transceiver, then by label, then by id) keeps the position, and the others
 * are set clockwise on a ring around it, starting due north. Nodes the twin
 * gives no position are lined up below the map, by label. Nothing is random:
 * the same topology is drawn the same way every time.
 */
export function geographicPositions(
  nodes: GraphNode[],
  positions: Record<string, GeoPosition> | undefined,
): Record<string, Point> | undefined {
  if (!positions) return undefined;
  const located = nodes.filter((n) => positions[n.id] !== undefined);
  if (located.length === 0) return undefined;
  const projected = project(
    Object.fromEntries(located.map((n) => [n.id, positions[n.id]])),
  );

  const groups = new Map<string, GraphNode[]>();
  for (const node of located) {
    const { latitude, longitude } = positions[node.id];
    const key = `${latitude},${longitude}`;
    const group = groups.get(key);
    if (group) group.push(node);
    else groups.set(key, [node]);
  }

  const out: Record<string, Point> = {};
  for (const group of groups.values()) {
    group.sort(byRoleThenName);
    const centre = projected[group[0].id];
    out[group[0].id] = { ...centre };
    const ring = group.length - 1;
    const radius = Math.max(RING_RADIUS, (ring * RING_SPACING) / (2 * Math.PI));
    group.slice(1).forEach((node, i) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * i) / ring;
      out[node.id] = {
        x: centre.x + radius * Math.cos(angle),
        y: centre.y + radius * Math.sin(angle),
      };
    });
  }

  const unplaced = nodes.filter((n) => positions[n.id] === undefined).sort(byRoleThenName);
  if (unplaced.length > 0) {
    const points = Object.values(out);
    const left = Math.min(...points.map((p) => p.x));
    const bottom = Math.max(...points.map((p) => p.y));
    unplaced.forEach((node, i) => {
      out[node.id] = { x: left + i * UNPLACED_SPACING, y: bottom + UNPLACED_GAP };
    });
  }
  return out;
}
