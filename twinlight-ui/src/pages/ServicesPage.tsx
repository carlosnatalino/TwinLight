import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { Link } from "react-router-dom";
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape, { type Core } from "cytoscape";
import dagre from "cytoscape-dagre";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { useServicesStore } from "@/store/services";
import { useServiceGraph } from "@/hooks/useServiceGraph";
import { modulationOf } from "@/api/types";
import type { ConnectivityService, ServiceInfoResponse } from "@/api/types";
import { extractName, getNodeDisplayName } from "@/lib/tapi-helpers";
import { createClient } from "@/api/client";
import { cn } from "@/lib/cn";
import { LAYOUT_CONFIGS, LAYOUT_LABELS, type LayoutName } from "@/lib/cytoscape-layout";
import {
  Loader2,
  RefreshCw,
  PlusCircle,
  Trash2,
  Network,
  ZoomIn,
  ZoomOut,
  Maximize2,
  ChevronDown,
  Activity,
} from "lucide-react";

try {
  cytoscape.use(dagre);
} catch {
  // already registered
}

// Stylesheet for the service logical topology.
// Nodes reuse physical topology styles; edges are colored by lifecycle-state class.
const SERVICE_STYLESHEET: Array<cytoscape.StylesheetStyle | cytoscape.StylesheetCSS> = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "text-valign": "bottom",
      "text-halign": "center",
      "text-margin-y": 8,
      "font-size": 11,
      color: "#374151",
      "text-wrap": "wrap",
      "text-max-width": "120px",
    },
  },
  {
    selector: "node[type='transceiver']",
    style: {
      shape: "round-rectangle",
      "background-color": "#3b82f6",
      width: 40,
      height: 28,
    },
  },
  {
    selector: "node[type='roadm']",
    style: {
      shape: "diamond",
      "background-color": "#22c55e",
      width: 44,
      height: 44,
    },
  },
  {
    selector: "node:selected",
    style: {
      "border-width": 3,
      "border-color": "#f59e0b",
      "border-style": "solid",
    },
  },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      "target-arrow-shape": "none",
      "line-color": "#94a3b8",
      width: 3,
      label: "data(label)",
      "font-size": 10,
      "text-rotation": "autorotate",
      "text-margin-y": -10,
      color: "#374151",
    },
  },
  {
    selector: "edge.installed",
    style: { "line-color": "#22c55e" },
  },
  {
    selector: "edge.planned",
    style: { "line-color": "#eab308" },
  },
  {
    selector: "edge.pending-removal",
    style: { "line-color": "#ef4444", "line-style": "dashed" },
  },
  {
    selector: "edge.potential-available",
    style: { "line-color": "#3b82f6" },
  },
  {
    selector: "edge.potential-busy",
    style: { "line-color": "#a855f7" },
  },
  {
    selector: "edge:selected",
    style: {
      "line-color": "#f59e0b",
      width: 5,
    },
  },
  {
    selector: "edge.hidden-label",
    style: { label: "" },
  },
];

const LIFECYCLE_COLORS: Record<string, string> = {
  INSTALLED: "bg-green-100 text-green-800",
  PLANNED: "bg-yellow-100 text-yellow-800",
  PENDING_REMOVAL: "bg-red-100 text-red-800",
  POTENTIAL_AVAILABLE: "bg-blue-100 text-blue-800",
  POTENTIAL_BUSY: "bg-purple-100 text-purple-800",
};

const ADMIN_COLORS: Record<string, string> = {
  UNLOCKED: "bg-green-50 text-green-700",
  LOCKED: "bg-slate-100 text-slate-600",
};

const ADMIN_STATES = ["UNLOCKED", "LOCKED"] as const;
const LIFECYCLE_STATES = [
  "PLANNED",
  "POTENTIAL_AVAILABLE",
  "POTENTIAL_BUSY",
  "INSTALLED",
  "PENDING_REMOVAL",
] as const;

function ServiceListItem({
  svc,
  isSelected,
  onClick,
}: {
  svc: ConnectivityService;
  isSelected: boolean;
  onClick: () => void;
}) {
  const name = extractName(svc.name, "service-name");
  const lifecycleState = svc["lifecycle-state"] ?? "";
  const adminState = svc["administrative-state"] ?? "";

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-slate-100 hover:bg-slate-50 transition-colors ${
        isSelected ? "bg-blue-50 border-l-2 border-l-blue-500" : ""
      }`}
    >
      <div className="font-mono text-xs text-slate-500">{svc.uuid.slice(0, 8)}&hellip;</div>
      {name && <div className="text-sm font-medium text-slate-800 mt-0.5 truncate">{name}</div>}
      <div className="flex gap-1 mt-1 flex-wrap">
        <span
          className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${
            LIFECYCLE_COLORS[lifecycleState] ?? "bg-slate-100 text-slate-600"
          }`}
        >
          {lifecycleState}
        </span>
        <span
          className={`inline-block rounded px-1.5 py-0.5 text-xs ${
            ADMIN_COLORS[adminState] ?? "bg-slate-100 text-slate-600"
          }`}
        >
          {adminState}
        </span>
      </div>
    </button>
  );
}

interface DetailPanelProps {
  service: ConnectivityService;
  serviceInfo: ServiceInfoResponse | null;
  serviceInfoLoading: boolean;
  sipToNodeName: Map<string, string>;
  onUpdate: (field: "administrative-state" | "lifecycle-state", value: string) => void;
  onDelete: () => void;
  onClose: () => void;
}

function ServiceDetailPanel({
  service,
  serviceInfo,
  serviceInfoLoading,
  sipToNodeName,
  onUpdate,
  onDelete,
  onClose,
}: DetailPanelProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const name = extractName(service.name, "service-name");
  const endpoints = service["end-point"] ?? [];
  const aEnd = endpoints[0];
  const zEnd = endpoints[1];

  async function handleDelete() {
    setIsDeleting(true);
    try {
      await onDelete();
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <aside className="w-80 shrink-0 flex flex-col bg-white border-l border-slate-200 overflow-y-auto">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
        <h2 className="text-sm font-semibold text-slate-800">Service Detail</h2>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-slate-600 text-lg leading-none"
          aria-label="Close"
        >
          &times;
        </button>
      </div>

      <div className="flex-1 px-4 py-3 space-y-4">
        {/* Identity */}
        <section>
          <p className="text-xs text-slate-500 mb-1">UUID</p>
          <p className="font-mono text-xs text-slate-700 break-all">{service.uuid}</p>
          {name && (
            <>
              <p className="text-xs text-slate-500 mt-2 mb-1">Name</p>
              <p className="text-sm text-slate-800">{name}</p>
            </>
          )}
          {modulationOf(service) && (
            <>
              <p className="text-xs text-slate-500 mt-2 mb-1">Modulation format</p>
              <p className="text-sm text-slate-800 font-mono">{modulationOf(service)}</p>
            </>
          )}
          {service["frequency-slot"] && (
            <>
              <p className="text-xs text-slate-500 mt-2 mb-1">Spectrum (L0)</p>
              <p className="text-sm text-slate-800 font-mono">
                {service["frequency-slot"]["nominal-central-frequency"].toFixed(4)} THz
                <span className="text-slate-500 mx-1">×</span>
                {service["frequency-slot"]["slot-width"].toFixed(2)} GHz
              </p>
            </>
          )}
        </section>

        {/* States */}
        <section className="space-y-3">
          <div>
            <label className="text-xs text-slate-500 block mb-1">Operational State</label>
            <span
              className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${
                service["operational-state"] === "ENABLED"
                  ? "bg-green-100 text-green-800"
                  : "bg-red-100 text-red-800"
              }`}
            >
              {service["operational-state"]}
            </span>
          </div>

          <div>
            <label className="text-xs text-slate-500 block mb-1">Administrative State</label>
            <div className="relative">
              <select
                value={service["administrative-state"]}
                onChange={(e) => onUpdate("administrative-state", e.target.value)}
                className="w-full text-sm border border-slate-200 rounded px-2 py-1.5 pr-7 bg-white appearance-none focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                {ADMIN_STATES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-2 h-3.5 w-3.5 text-slate-400" />
            </div>
          </div>

          <div>
            <label className="text-xs text-slate-500 block mb-1">Lifecycle State</label>
            <div className="relative">
              <select
                value={service["lifecycle-state"]}
                onChange={(e) => onUpdate("lifecycle-state", e.target.value)}
                className="w-full text-sm border border-slate-200 rounded px-2 py-1.5 pr-7 bg-white appearance-none focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                {LIFECYCLE_STATES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-2 h-3.5 w-3.5 text-slate-400" />
            </div>
          </div>
        </section>

        {/* Path and distance (from /internal/services/{uuid}) */}
        <section className="space-y-2">
          <label className="text-xs text-slate-500 block">Path & distance</label>
          {serviceInfoLoading ? (
            <p className="text-xs text-slate-400">Loading path…</p>
          ) : serviceInfo?.hops && serviceInfo.hops.length > 0 ? (
            <div className="space-y-2">
              {serviceInfo["total-fiber-km"] != null && (
                <p className="text-sm text-slate-700">
                  Total fiber: <span className="font-medium">{serviceInfo["total-fiber-km"].toFixed(1)} km</span>
                </p>
              )}
              <div className="overflow-x-auto pb-1">
                <div className="flex items-center min-w-max gap-0 flex-wrap">
                  {serviceInfo.hops.map((hop) => (
                    <span key={hop.uid} className="flex items-center">
                      <span
                        className={cn(
                          "inline-flex flex-col items-center rounded border px-2 py-1 text-xs leading-tight",
                          hop.type === "Transceiver"
                            ? "bg-blue-50 border-blue-200 text-blue-800"
                            : "bg-violet-50 border-violet-200 text-violet-800"
                        )}
                      >
                        <span className="text-[9px] font-semibold uppercase opacity-70">
                          {hop.type === "Transceiver" ? "TRX" : "ROADM"}
                        </span>
                        <span className="font-mono whitespace-nowrap">{hop.uid}</span>
                      </span>
                      {hop.distance_km_to_next != null && (
                        <span className="flex items-center text-slate-400 px-1">
                          <span className="block h-px w-3 bg-slate-300" />
                          <span className="text-[10px] px-1 whitespace-nowrap text-slate-500">
                            {hop.distance_km_to_next.toFixed(1)} km
                          </span>
                          <span className="block h-px w-3 bg-slate-300" />
                          <span className="text-slate-400 text-[9px]">→</span>
                        </span>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ) : serviceInfo && !serviceInfoLoading ? (
            <p className="text-xs text-slate-400 italic">
              Path unavailable (GNPy not configured or path not computed).
            </p>
          ) : null}
        </section>

        {/* Endpoints */}
        <section className="space-y-3">
          {aEnd && (
            <div className="rounded border border-blue-100 bg-blue-50 p-2.5">
              <p className="text-xs font-medium text-blue-700 mb-1">A-End ({aEnd["local-id"]})</p>
              <p className="text-xs text-slate-500">SIP</p>
              <p className="font-mono text-xs text-slate-700 break-all">
                {aEnd["service-interface-point"]["service-interface-point-uuid"]}
              </p>
              {sipToNodeName.get(
                aEnd["service-interface-point"]["service-interface-point-uuid"]
              ) && (
                <p className="text-xs text-slate-600 mt-1">
                  Node:{" "}
                  {sipToNodeName.get(
                    aEnd["service-interface-point"]["service-interface-point-uuid"]
                  )}
                </p>
              )}
              <p className="text-xs text-slate-500 mt-1">
                Direction: <span className="text-slate-700">{aEnd.direction}</span>
              </p>
            </div>
          )}

          {zEnd && (
            <div className="rounded border border-green-100 bg-green-50 p-2.5">
              <p className="text-xs font-medium text-green-700 mb-1">Z-End ({zEnd["local-id"]})</p>
              <p className="text-xs text-slate-500">SIP</p>
              <p className="font-mono text-xs text-slate-700 break-all">
                {zEnd["service-interface-point"]["service-interface-point-uuid"]}
              </p>
              {sipToNodeName.get(
                zEnd["service-interface-point"]["service-interface-point-uuid"]
              ) && (
                <p className="text-xs text-slate-600 mt-1">
                  Node:{" "}
                  {sipToNodeName.get(
                    zEnd["service-interface-point"]["service-interface-point-uuid"]
                  )}
                </p>
              )}
              <p className="text-xs text-slate-500 mt-1">
                Direction: <span className="text-slate-700">{zEnd.direction}</span>
              </p>
            </div>
          )}
        </section>

        {/* View monitoring */}
        <section>
          <Link
            to={`/monitoring?service=${encodeURIComponent(service.uuid)}`}
            className="flex items-center justify-center gap-2 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-100 hover:border-slate-300 transition-colors"
          >
            <Activity className="h-4 w-4 text-slate-500" />
            View monitoring
          </Link>
        </section>
      </div>

      {/* Delete */}
      <div className="px-4 py-3 border-t border-slate-200">
        {!confirmDelete ? (
          <button
            onClick={() => setConfirmDelete(true)}
            className="flex items-center gap-1.5 text-sm text-red-600 hover:text-red-700"
          >
            <Trash2 className="h-4 w-4" /> Delete service
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-slate-600">Delete this service permanently?</p>
            <div className="flex gap-2">
              <button
                onClick={handleDelete}
                disabled={isDeleting}
                className="flex items-center gap-1 rounded bg-red-600 px-3 py-1.5 text-xs text-white hover:bg-red-700 disabled:opacity-60"
              >
                {isDeleting && <Loader2 className="h-3 w-3 animate-spin" />}
                Confirm
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

export default function ServicesPage() {
  const { baseUrl } = useConnectionStore();
  const { data: topologyData, fetchTopology } = useTopologyStore();
  const { services, isLoading, error, fetchServices, deleteService, updateService } =
    useServicesStore();

  const [selectedUuid, setSelectedUuid] = useState<string | null>(null);
  const [layoutName, setLayoutName] = useState<LayoutName>("cose");
  const [showLabels, setShowLabels] = useState(true);
  const [showLayoutMenu, setShowLayoutMenu] = useState(false);
  const [serviceInfo, setServiceInfo] = useState<ServiceInfoResponse | null>(null);
  const [serviceInfoLoading, setServiceInfoLoading] = useState(false);
  const cyRef = useRef<Core | null>(null);

  useEffect(() => {
    if (baseUrl) {
      void fetchServices(baseUrl);
      if (!topologyData) void fetchTopology(baseUrl);
    }
  }, [baseUrl, fetchServices, fetchTopology, topologyData]);

  // Fetch path/hops and total-fiber-km when a service is selected
  useEffect(() => {
    if (!baseUrl || !selectedUuid) {
      setServiceInfo(null);
      setServiceInfoLoading(false);
      return;
    }
    setServiceInfoLoading(true);
    setServiceInfo(null);
    const client = createClient(baseUrl);
    client
      .getServiceInfo(selectedUuid)
      .then(setServiceInfo)
      .catch(() => setServiceInfo(null))
      .finally(() => setServiceInfoLoading(false));
  }, [baseUrl, selectedUuid]);

  const elements = useServiceGraph(services, topologyData);

  // SIP UUID → node display name (for detail panel)
  const sipToNodeName = useMemo(() => {
    const map = new Map<string, string>();
    if (!topologyData) return map;
    for (const topo of topologyData.topologies) {
      for (const node of topo.node) {
        for (const nep of node["owned-node-edge-point"]) {
          for (const sipRef of nep["mapped-service-interface-point"] ?? []) {
            map.set(sipRef["service-interface-point-uuid"], getNodeDisplayName(node));
          }
        }
      }
    }
    return map;
  }, [topologyData]);

  const selectedService = useMemo(
    () => services.find((s) => s.uuid === selectedUuid) ?? null,
    [services, selectedUuid]
  );

  // Sync Cytoscape selection with selectedUuid
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().unselect();
    if (selectedUuid) {
      cy.getElementById(selectedUuid).select();
    }
  }, [selectedUuid]);

  // Re-run layout when layout name changes or when elements first become available
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || elements.length === 0) return;
    cy.layout(LAYOUT_CONFIGS[layoutName] || LAYOUT_CONFIGS.cose).run();
  }, [layoutName, elements]);

  // Toggle labels
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (showLabels) {
      cy.elements().removeClass("hidden-label");
    } else {
      cy.edges().addClass("hidden-label");
    }
  }, [showLabels]);

  const handleCy = useCallback(
    (cy: Core) => {
      cyRef.current = cy;

      cy.on("tap", "edge", (evt) => {
        const edge = evt.target;
        setSelectedUuid(edge.id() as string);
      });

      cy.on("tap", "node", () => {
        setSelectedUuid(null);
      });

      cy.on("tap", (evt) => {
        if (evt.target === cy) setSelectedUuid(null);
      });
    },
    []
  );

  const handleZoomIn = () => {
    const cy = cyRef.current;
    if (cy)
      cy.zoom({
        level: cy.zoom() * 1.25,
        renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
      });
  };
  const handleZoomOut = () => {
    const cy = cyRef.current;
    if (cy)
      cy.zoom({
        level: cy.zoom() / 1.25,
        renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
      });
  };
  const handleFit = () => cyRef.current?.fit();

  async function handleUpdate(
    field: "administrative-state" | "lifecycle-state",
    value: string
  ) {
    if (!selectedUuid) return;
    await updateService(baseUrl, selectedUuid, {
      "tapi-connectivity:connectivity-service": { [field]: value },
    });
  }

  async function handleDelete() {
    if (!selectedUuid) return;
    await deleteService(baseUrl, selectedUuid);
    setSelectedUuid(null);
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header bar */}
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4">
        <h1 className="text-sm font-semibold text-slate-800">
          Services{" "}
          <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-normal text-slate-500">
            {services.length}
          </span>
        </h1>

        <div className="flex-1" />

        {/* Layout picker */}
        <div className="relative">
          <button
            onClick={() => setShowLayoutMenu((v) => !v)}
            className="flex items-center gap-1 rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
          >
            {LAYOUT_LABELS[layoutName]}
            <ChevronDown className="h-3 w-3" />
          </button>
          {showLayoutMenu && (
            <div className="absolute right-0 top-full z-10 mt-1 w-40 rounded border border-slate-200 bg-white py-1 shadow-lg">
              {(Object.keys(LAYOUT_LABELS) as LayoutName[]).map((name) => (
                <button
                  key={name}
                  onClick={() => {
                    setLayoutName(name);
                    setShowLayoutMenu(false);
                  }}
                  className={`w-full px-3 py-1.5 text-left text-xs hover:bg-slate-50 ${
                    name === layoutName ? "font-medium text-blue-600" : "text-slate-700"
                  }`}
                >
                  {LAYOUT_LABELS[name]}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Label toggle */}
        <button
          onClick={() => setShowLabels((v) => !v)}
          title="Toggle labels"
          className={`rounded border px-2.5 py-1 text-xs transition-colors ${
            showLabels
              ? "border-blue-300 bg-blue-50 text-blue-700"
              : "border-slate-200 text-slate-600 hover:bg-slate-50"
          }`}
        >
          Labels
        </button>

        {/* Zoom controls */}
        <div className="flex items-center gap-0.5">
          <button
            onClick={handleZoomIn}
            className="rounded border border-slate-200 p-1 text-slate-600 hover:bg-slate-50"
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={handleZoomOut}
            className="rounded border border-slate-200 p-1 text-slate-600 hover:bg-slate-50"
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={handleFit}
            className="rounded border border-slate-200 p-1 text-slate-600 hover:bg-slate-50"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
        </div>

        <button
          onClick={() => void fetchServices(baseUrl)}
          disabled={isLoading}
          className="flex items-center gap-1.5 rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-60"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </button>

        <Link
          to="/services/new"
          className="flex items-center gap-1.5 rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700"
        >
          <PlusCircle className="h-3.5 w-3.5" />
          New Service
        </Link>
      </div>

      {/* Error bar */}
      {error && (
        <div className="shrink-0 bg-red-50 px-4 py-2 text-xs text-red-700 border-b border-red-100">
          {error}
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Service list (left) */}
        <aside className="w-56 shrink-0 flex flex-col border-r border-slate-200 bg-white overflow-y-auto">
          {isLoading && services.length === 0 ? (
            <div className="flex flex-1 items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
            </div>
          ) : services.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 px-4 text-center">
              <Network className="h-8 w-8 text-slate-300 mb-2" />
              <p className="text-xs text-slate-500">No services yet</p>
              <Link
                to="/services/new"
                className="mt-3 text-xs text-blue-600 hover:underline"
              >
                + Add one
              </Link>
            </div>
          ) : (
            services.map((svc) => (
              <ServiceListItem
                key={svc.uuid}
                svc={svc}
                isSelected={svc.uuid === selectedUuid}
                onClick={() =>
                  setSelectedUuid((prev) => (prev === svc.uuid ? null : svc.uuid))
                }
              />
            ))
          )}
        </aside>

        {/* Graph (center) */}
        <div className="flex-1 overflow-hidden relative">
          {isLoading && elements.length === 0 && (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-slate-300" />
            </div>
          )}

          {!isLoading && elements.length === 0 && (
            <div className="flex h-full items-center justify-center">
              <div className="text-center">
                <Network className="mx-auto h-10 w-10 text-slate-200 mb-3" />
                <p className="text-sm text-slate-400">
                  {services.length === 0
                    ? "Create a service to see the logical topology"
                    : "Services could not be mapped to physical nodes"}
                </p>
              </div>
            </div>
          )}

          {elements.length > 0 && (
            <CytoscapeComponent
              elements={elements}
              stylesheet={SERVICE_STYLESHEET}
              layout={LAYOUT_CONFIGS[layoutName] || LAYOUT_CONFIGS.cose}
              style={{ width: "100%", height: "100%" }}
              cy={handleCy}
            />
          )}
        </div>

        {/* Detail panel (right) */}
        {selectedService && (
          <ServiceDetailPanel
            service={selectedService}
            serviceInfo={serviceInfo}
            serviceInfoLoading={serviceInfoLoading}
            sipToNodeName={sipToNodeName}
            onUpdate={(field, value) => void handleUpdate(field, value)}
            onDelete={() => handleDelete()}
            onClose={() => setSelectedUuid(null)}
          />
        )}
      </div>
    </div>
  );
}
