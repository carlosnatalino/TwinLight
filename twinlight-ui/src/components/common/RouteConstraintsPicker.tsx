import { X } from "lucide-react";
import type { RoutableNode } from "@/lib/routableNodes";
import type { NodeRef, RouteConstraints } from "@/lib/serviceRequest";

interface ListProps {
  title: string;
  hint: string;
  nodes: RoutableNode[];
  value: NodeRef[];
  taken: Set<string>;
  onChange: (value: NodeRef[]) => void;
}

function NodeList({ title, hint, nodes, value, taken, onChange }: ListProps) {
  const label = (ref: NodeRef) =>
    nodes.find((n) => n.ref["node-uuid"] === ref["node-uuid"])?.label ?? ref["node-uuid"].slice(0, 8);
  const available = nodes.filter((n) => !taken.has(n.ref["node-uuid"]));
  return (
    <div>
      <p className="block text-sm font-medium text-gray-700 mb-1">{title}</p>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {value.length === 0 && <span className="text-xs text-gray-400">None</span>}
        {value.map((ref, i) => (
          <span
            key={ref["node-uuid"]}
            className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
          >
            {title.startsWith("Pass") ? `${i + 1}. ` : ""}
            {label(ref)}
            <button
              type="button"
              aria-label={`Remove ${label(ref)}`}
              onClick={() => onChange(value.filter((_, j) => j !== i))}
              className="text-gray-400 hover:text-gray-700"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      <select
        value=""
        disabled={available.length === 0}
        onChange={(e) => {
          const node = nodes.find((n) => n.ref["node-uuid"] === e.target.value);
          if (node) onChange([...value, node.ref]);
        }}
        className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
      >
        <option value="">— Add a node —</option>
        {available.map((n) => (
          <option key={n.ref["node-uuid"]} value={n.ref["node-uuid"]}>
            {n.label}
          </option>
        ))}
      </select>
      <p className="mt-1 text-xs text-gray-400">{hint}</p>
    </div>
  );
}

/** Include (in order) and exclude ROADMs for a route. */
export function RouteConstraintsPicker({
  nodes,
  value,
  onChange,
}: {
  nodes: RoutableNode[];
  value: RouteConstraints;
  onChange: (value: RouteConstraints) => void;
}) {
  const taken = new Set([...value.include, ...value.exclude].map((n) => n["node-uuid"]));
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <NodeList
        title="Pass through (in order)"
        hint="The route visits these nodes in the order listed."
        nodes={nodes}
        value={value.include}
        taken={taken}
        onChange={(include) => onChange({ ...value, include })}
      />
      <NodeList
        title="Avoid"
        hint="The route never touches these nodes."
        nodes={nodes}
        value={value.exclude}
        taken={taken}
        onChange={(exclude) => onChange({ ...value, exclude })}
      />
    </div>
  );
}
