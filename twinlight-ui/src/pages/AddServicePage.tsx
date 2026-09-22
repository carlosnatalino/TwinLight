import { useState, useMemo, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle, XCircle, Loader2, PlusCircle, ArrowLeft } from "lucide-react";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { createClient, ApiError } from "@/api/client";
import { extractName } from "@/lib/tapi-helpers";
import { tapiEndPoint } from "@/api/types";
import type { ModulationFormat, ServiceInterfacePoint, TapiNode } from "@/api/types";
import { cn } from "@/lib/cn";

// ─── Constants ───────────────────────────────────────────────────────────────

const MODULATION_FORMATS: ModulationFormat[] = ["DP-QPSK", "DP-16QAM", "DP-64QAM"];
const DIRECTIONS = ["BIDIRECTIONAL", "SINK", "SOURCE", "UNDEFINED_OR_UNKNOWN"] as const;
const ADMIN_STATES = ["UNLOCKED", "LOCKED"] as const;
const LIFECYCLE_STATES = [
  "PLANNED",
  "POTENTIAL_AVAILABLE",
  "POTENTIAL_BUSY",
  "INSTALLED",
  "PENDING_REMOVAL",
] as const;

// ─── Helpers ─────────────────────────────────────────────────────────────────

function getSipDisplayName(sip: ServiceInterfacePoint, ownerNode: TapiNode | undefined): string {
  const sipName =
    extractName(sip.name, "sip-name") || extractName(sip.name, "name") || sip.uuid.slice(0, 8);
  const nodeName = ownerNode
    ? extractName(ownerNode.name, "node-name") ||
      extractName(ownerNode.name, "name") ||
      ownerNode.uuid.slice(0, 8)
    : null;
  const proto = sip["layer-protocol-name"] ?? "?";
  return nodeName ? `${nodeName} / ${sipName} (${proto})` : `${sipName} (${proto})`;
}

// ─── Sub-components ──────────────────────────────────────────────────────────

interface FormGroupProps {
  label: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}

function FormGroup({ label, required, hint, children }: FormGroupProps) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-gray-400">{hint}</p>}
    </div>
  );
}

interface SelectProps {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  children: React.ReactNode;
  hasError?: boolean;
}

function Select({ value, onChange, disabled, children, hasError }: SelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={cn(
        "w-full rounded-md border px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1 bg-white",
        hasError
          ? "border-red-400 focus:border-red-500 focus:ring-red-500"
          : "border-gray-300 focus:border-blue-500 focus:ring-blue-500",
        disabled && "bg-gray-50 text-gray-400 cursor-not-allowed"
      )}
    >
      {children}
    </select>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function AddServicePage() {
  const navigate = useNavigate();
  const { baseUrl } = useConnectionStore();
  const { data, fetchTopology } = useTopologyStore();

  // Fetch topology if not available (needed to populate SIP dropdowns)
  useEffect(() => {
    if (baseUrl && !data) void fetchTopology(baseUrl);
  }, [baseUrl, data, fetchTopology]);

  // ── Form state ────────────────────────────────────────────────────────────
  const [serviceName, setServiceName] = useState("");
  const [aEndSip, setAEndSip] = useState("");
  const [zEndSip, setZEndSip] = useState("");
  const [aEndDir, setAEndDir] = useState<string>("BIDIRECTIONAL");
  const [zEndDir, setZEndDir] = useState<string>("BIDIRECTIONAL");
  const [adminState, setAdminState] = useState<string>("UNLOCKED");
  const [lifecycleState, setLifecycleState] = useState<string>("PLANNED");
  const [modulationFormat, setModulationFormat] = useState<ModulationFormat>("DP-QPSK");

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [createdUuid, setCreatedUuid] = useState<string | null>(null);

  // ── Derived data ──────────────────────────────────────────────────────────

  // Build a SIP → owning node map
  const sipOwnerMap = useMemo(() => {
    const map = new Map<string, TapiNode>();
    for (const topo of data?.topologies ?? []) {
      for (const node of topo.node) {
        for (const nep of node["owned-node-edge-point"]) {
          for (const sipRef of nep["mapped-service-interface-point"] ?? []) {
            map.set(sipRef["service-interface-point-uuid"], node);
          }
        }
      }
    }
    return map;
  }, [data]);

  const sips: ServiceInterfacePoint[] = data?.sips ?? [];

  // Validation
  const aEndError = aEndSip && zEndSip && aEndSip === zEndSip ? "Must differ from Z-End" : null;
  const zEndError = aEndSip && zEndSip && aEndSip === zEndSip ? "Must differ from A-End" : null;
  const canSubmit = !!aEndSip && !!zEndSip && aEndSip !== zEndSip && !isSubmitting;

  // ── Handlers ──────────────────────────────────────────────────────────────

  function resetForm() {
    setServiceName("");
    setAEndSip("");
    setZEndSip("");
    setAEndDir("BIDIRECTIONAL");
    setZEndDir("BIDIRECTIONAL");
    setAdminState("UNLOCKED");
    setLifecycleState("PLANNED");
    setModulationFormat("DP-QPSK");
    setErrorMsg(null);
    setCreatedUuid(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setIsSubmitting(true);
    setErrorMsg(null);

    const body = {
      "tapi-connectivity:connectivity-service": {
        ...(serviceName.trim()
          ? { name: [{ "value-name": "service-name", value: serviceName.trim() }] }
          : {}),
        "end-point": [
          // Modulation rides on the end-point: T-API v2.6.0 has no
          // modulation leaf on the connectivity-service itself.
          tapiEndPoint("a-end", aEndSip, modulationFormat, aEndDir),
          tapiEndPoint("z-end", zEndSip, modulationFormat, zEndDir),
        ],
        "administrative-state": adminState,
        "lifecycle-state": lifecycleState,
      },
    };

    try {
      const client = createClient(baseUrl);
      const result = await client.createConnectivityService(body);
      setCreatedUuid(result["tapi-connectivity:connectivity-service"].uuid);
    } catch (err) {
      if (err instanceof ApiError) {
        // Try to parse a structured error detail from the response body
        let detail = err.message;
        try {
          const parsed = JSON.parse(err.message.replace(/^HTTP \d+: /, ""));
          if (typeof parsed?.detail === "string") detail = parsed.detail;
        } catch {
          // keep raw message
        }
        setErrorMsg(detail);
      } else {
        setErrorMsg(err instanceof Error ? err.message : "Unknown error");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  // ── Success state ─────────────────────────────────────────────────────────

  if (createdUuid) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <div className="rounded-xl border border-green-200 bg-green-50 p-8 text-center shadow-sm">
          <CheckCircle className="mx-auto h-12 w-12 text-green-500 mb-4" />
          <h2 className="text-xl font-bold text-green-800 mb-2">Service Created</h2>
          <p className="text-sm text-green-700 mb-4">
            Connectivity service has been successfully provisioned.
          </p>
          <div className="inline-block rounded-lg bg-white border border-green-200 px-4 py-2 mb-6">
            <p className="text-xs text-gray-500 mb-0.5">Service UUID</p>
            <p className="font-mono text-sm text-gray-800 break-all">{createdUuid}</p>
          </div>
          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <button
              onClick={resetForm}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-green-600 px-5 py-2 text-sm font-medium text-white hover:bg-green-700 transition-colors"
            >
              <PlusCircle className="h-4 w-4" />
              Create Another
            </button>
            <button
              onClick={() => navigate("/services")}
              className="inline-flex items-center justify-center gap-2 rounded-md border border-gray-300 bg-white px-5 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              View Services
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Form ──────────────────────────────────────────────────────────────────

  const noSips = sips.length === 0;

  return (
    <div className="mx-auto max-w-2xl p-6 space-y-6">
      {/* Header */}
      <div>
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 transition-colors mb-4"
        >
          <ArrowLeft className="h-4 w-4" /> Back
        </button>
        <h1 className="text-2xl font-bold text-gray-900">Add Connectivity Service</h1>
        <p className="mt-1 text-sm text-gray-500">
          Provision a new point-to-point service between two Service Interface Points.
        </p>
      </div>

      {/* No topology data warning */}
      {noSips && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          No Service Interface Points available. Make sure the Digital Twin is connected and topology
          data is loaded (visit the{" "}
          <button
            onClick={() => navigate("/topology")}
            className="underline hover:no-underline font-medium"
          >
            Topology
          </button>{" "}
          page to trigger a refresh).
        </div>
      )}

      <form onSubmit={(e) => void handleSubmit(e)} className="space-y-6">
        {/* ── Service Identity ────────────────────────────────────────────── */}
        <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
          <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
            Service Identity
          </h2>

          <FormGroup label="Service Name" hint="Optional human-readable label stored as a name/value pair.">
            <input
              type="text"
              value={serviceName}
              onChange={(e) => setServiceName(e.target.value)}
              placeholder="e.g. Amsterdam–Frankfurt-100G"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </FormGroup>
        </section>

        {/* ── Endpoints ───────────────────────────────────────────────────── */}
        <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-5">
          <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
            Endpoints
          </h2>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            {/* A-End */}
            <div className="space-y-4 rounded-lg border border-blue-100 bg-blue-50/50 p-4">
              <p className="text-xs font-semibold text-blue-700 uppercase tracking-wide">A-End</p>
              <FormGroup label="Service Interface Point" required>
                <Select
                  value={aEndSip}
                  onChange={setAEndSip}
                  disabled={noSips}
                  hasError={!!aEndError}
                >
                  <option value="">— Select SIP —</option>
                  {sips.map((sip) => (
                    <option key={sip.uuid} value={sip.uuid}>
                      {getSipDisplayName(sip, sipOwnerMap.get(sip.uuid))}
                    </option>
                  ))}
                </Select>
                {aEndError && <p className="mt-1 text-xs text-red-600">{aEndError}</p>}
                {aEndSip && (
                  <p className="mt-1 font-mono text-xs text-gray-400 truncate" title={aEndSip}>
                    {aEndSip}
                  </p>
                )}
              </FormGroup>
              <FormGroup label="Direction">
                <Select value={aEndDir} onChange={setAEndDir}>
                  {DIRECTIONS.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </Select>
              </FormGroup>
            </div>

            {/* Z-End */}
            <div className="space-y-4 rounded-lg border border-green-100 bg-green-50/50 p-4">
              <p className="text-xs font-semibold text-green-700 uppercase tracking-wide">Z-End</p>
              <FormGroup label="Service Interface Point" required>
                <Select
                  value={zEndSip}
                  onChange={setZEndSip}
                  disabled={noSips}
                  hasError={!!zEndError}
                >
                  <option value="">— Select SIP —</option>
                  {sips.map((sip) => (
                    <option key={sip.uuid} value={sip.uuid}>
                      {getSipDisplayName(sip, sipOwnerMap.get(sip.uuid))}
                    </option>
                  ))}
                </Select>
                {zEndError && <p className="mt-1 text-xs text-red-600">{zEndError}</p>}
                {zEndSip && (
                  <p className="mt-1 font-mono text-xs text-gray-400 truncate" title={zEndSip}>
                    {zEndSip}
                  </p>
                )}
              </FormGroup>
              <FormGroup label="Direction">
                <Select value={zEndDir} onChange={setZEndDir}>
                  {DIRECTIONS.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </Select>
              </FormGroup>
            </div>
          </div>
        </section>

        {/* ── Service Parameters ──────────────────────────────────────────── */}
        <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
          <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
            Service Parameters
          </h2>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <FormGroup
              label="Administrative State"
              hint="UNLOCKED enables the service; LOCKED suspends it."
            >
              <Select value={adminState} onChange={setAdminState}>
                {ADMIN_STATES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
            </FormGroup>

            <FormGroup
              label="Lifecycle State"
              hint="PLANNED is typical for newly provisioned services."
            >
              <Select value={lifecycleState} onChange={setLifecycleState}>
                {LIFECYCLE_STATES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ")}
                  </option>
                ))}
              </Select>
            </FormGroup>

            <FormGroup
              label="Modulation Format"
              hint="Sets the coherent modulation scheme used for QoT calculations."
            >
              <Select
                value={modulationFormat}
                onChange={(v) => setModulationFormat(v as ModulationFormat)}
              >
                {MODULATION_FORMATS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </Select>
            </FormGroup>
          </div>
        </section>

        {/* ── Error message ────────────────────────────────────────────────── */}
        {errorMsg && (
          <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3">
            <XCircle className="h-5 w-5 text-red-500 shrink-0 mt-0.5" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-red-700">Failed to create service</p>
              <p className="text-sm text-red-600 mt-0.5 break-words">{errorMsg}</p>
            </div>
          </div>
        )}

        {/* ── Submit ──────────────────────────────────────────────────────── */}
        <div className="flex items-center gap-4">
          <button
            type="submit"
            disabled={!canSubmit}
            className={cn(
              "inline-flex items-center gap-2 rounded-md px-6 py-2.5 text-sm font-medium transition-colors",
              canSubmit
                ? "bg-blue-600 text-white hover:bg-blue-700"
                : "bg-gray-200 text-gray-400 cursor-not-allowed"
            )}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Creating…
              </>
            ) : (
              <>
                <PlusCircle className="h-4 w-4" />
                Create Service
              </>
            )}
          </button>
          {!aEndSip || !zEndSip ? (
            <p className="text-xs text-gray-400">Select both A-End and Z-End SIPs to continue.</p>
          ) : aEndSip === zEndSip ? (
            <p className="text-xs text-red-500">A-End and Z-End SIPs must be different.</p>
          ) : null}
        </div>
      </form>
    </div>
  );
}
