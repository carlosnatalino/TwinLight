import { describe, it, expect } from "vitest";
import { inventoryFromPhysicalContext } from "@/lib/inventory";
import type { PhysicalContextResponse } from "@/api/types";

const name = (uid: string) => [{ "value-name": "element-uid", value: uid }];

const context: PhysicalContextResponse = {
  "tapi-equipment:physical-context": {
    device: [
      {
        uuid: "d1",
        name: name("roadm A"),
        equipment: [
          {
            uuid: "e1",
            name: name("roadm A"),
            "actual-equipment": {
              "common-equipment-properties": { "equipment-type-name": "Roadm" },
            },
          },
        ],
      },
      {
        uuid: "d2",
        name: name("amp 1"),
        equipment: [
          {
            uuid: "e2",
            name: name("amp 1"),
            "actual-equipment": {
              "common-equipment-properties": {
                "equipment-type-name": "Edfa",
                "equipment-type-identifier": "std_medium_gain",
              },
            },
          },
        ],
      },
    ],
    "physical-span": [{ uuid: "s1", name: name("fiber (A → B)") }],
  },
};

describe("inventoryFromPhysicalContext", () => {
  it("flattens devices' equipment and spans, joining fiber lengths", () => {
    const items = inventoryFromPhysicalContext(context, {
      "twinlight-transparent:data": {
        ElementList: { "fiber (A → B)": { type: "Fiber", length_km: 80 } },
      },
    });
    expect(items).toEqual([
      { uuid: "e1", uid: "roadm A", type: "Roadm", variety: undefined },
      { uuid: "e2", uid: "amp 1", type: "Edfa", variety: "std_medium_gain" },
      { uuid: "s1", uid: "fiber (A → B)", type: "Fiber", lengthKm: 80 },
    ]);
  });

  it("renders without the transparent element list", () => {
    const items = inventoryFromPhysicalContext(context, null);
    expect(items.find((i) => i.type === "Fiber")?.lengthKm).toBeUndefined();
    expect(items).toHaveLength(3);
  });
});
