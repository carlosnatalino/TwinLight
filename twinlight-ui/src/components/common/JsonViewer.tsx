import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";

interface JsonViewerProps {
  data: unknown;
  defaultExpanded?: boolean;
  className?: string;
}

function JsonNode({
  value,
  depth = 0,
  defaultExpanded = true,
}: {
  value: unknown;
  depth?: number;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded || depth < 2);

  if (value === null) return <span className="text-gray-400">null</span>;
  if (value === undefined) return <span className="text-gray-400">undefined</span>;
  if (typeof value === "boolean")
    return <span className="text-purple-600">{String(value)}</span>;
  if (typeof value === "number") return <span className="text-blue-600">{value}</span>;
  if (typeof value === "string") return <span className="text-green-700">"{value}"</span>;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-gray-500">[]</span>;
    return (
      <span>
        <button
          onClick={() => setExpanded(!expanded)}
          className="inline-flex items-center gap-0.5 text-gray-600 hover:text-gray-900"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
          <span className="text-gray-500 text-xs">[{value.length}]</span>
        </button>
        {expanded && (
          <div className="ml-4 border-l border-gray-200 pl-2">
            {value.map((item, i) => (
              <div key={i} className="py-0.5">
                <span className="text-gray-400 text-xs">{i}: </span>
                <JsonNode value={item} depth={depth + 1} defaultExpanded={false} />
              </div>
            ))}
          </div>
        )}
      </span>
    );
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="text-gray-500">{"{}"}</span>;
    return (
      <span>
        <button
          onClick={() => setExpanded(!expanded)}
          className="inline-flex items-center gap-0.5 text-gray-600 hover:text-gray-900"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
          <span className="text-gray-500 text-xs">{"{"}...{"}"}</span>
        </button>
        {expanded && (
          <div className="ml-4 border-l border-gray-200 pl-2">
            {entries.map(([key, val]) => (
              <div key={key} className="py-0.5">
                <span className="text-orange-700 font-medium text-xs">"{key}"</span>
                <span className="text-gray-400 mx-1">:</span>
                <JsonNode value={val} depth={depth + 1} defaultExpanded={false} />
              </div>
            ))}
          </div>
        )}
      </span>
    );
  }

  return <span>{String(value)}</span>;
}

export default function JsonViewer({ data, defaultExpanded = true, className }: JsonViewerProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div className={cn("rounded-lg border border-gray-200", className)}>
      <button
        onClick={() => setVisible(!visible)}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-sm font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg transition-colors"
      >
        {visible ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        Raw JSON
      </button>
      {visible && (
        <div className="p-4 overflow-x-auto">
          <pre className="font-mono text-xs leading-relaxed">
            <JsonNode value={data} defaultExpanded={defaultExpanded} />
          </pre>
        </div>
      )}
    </div>
  );
}
