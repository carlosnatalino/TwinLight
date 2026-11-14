import type {
  PhysicalContextResponse,
  TransparentElementListResponse,
} from "@/api/types";

/** One row of the Equipment page: a device's equipment or a physical span. */
export interface InventoryItem {
  uuid: string;
  /** The GNPy element UID, from the object's "element-uid" name. */
  uid: string;
  /** "Transceiver" | "Roadm" | "Edfa" | "Fiber" (GNPy kind). */
  type: string;
  /** GNPy type_variety, when the equipment carries one. */
  variety?: string;
  /** Fiber length [km]; T-API has no leaf for it, so it comes from /transparent. */
  lengthKm?: number;
}

function elementUid(name: { "value-name": string; value: string }[], fallback: string): string {
  return name.find((n) => n["value-name"] === "element-uid")?.value ?? fallback;
}

/**
 * Flatten T-API v2.6.0 physical-context into rows: every device's equipment,
 * then every physical span (a fiber). Lengths are joined from the transparent
 * controller's element list when it is available.
 */
export function inventoryFromPhysicalContext(
  context: PhysicalContextResponse,
  elements?: TransparentElementListResponse | null
): InventoryItem[] {
  const pc = context["tapi-equipment:physical-context"];
  const attrs = elements?.["twinlight-transparent:data"]?.ElementList ?? {};
  const items: InventoryItem[] = [];
  for (const device of pc.device ?? []) {
    for (const eq of device.equipment ?? []) {
      const props = eq["actual-equipment"]["common-equipment-properties"];
      items.push({
        uuid: eq.uuid,
        uid: elementUid(eq.name, eq.uuid),
        type: props["equipment-type-name"],
        variety: props["equipment-type-identifier"],
      });
    }
  }
  for (const span of pc["physical-span"] ?? []) {
    const uid = elementUid(span.name, span.uuid);
    const length = attrs[uid]?.length_km;
    items.push({
      uuid: span.uuid,
      uid,
      type: "Fiber",
      lengthKm: typeof length === "number" ? length : undefined,
    });
  }
  return items;
}
