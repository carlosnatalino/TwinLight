import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { RefreshCw, Wifi, WifiOff, AlertCircle, Clock } from "lucide-react";
import { cn } from "@/lib/cn";

function StatusIndicator() {
  const status = useConnectionStore((s) => s.status);
  const lastSuccessAt = useConnectionStore((s) => s.lastSuccessAt);

  const config = {
    connected: { icon: Wifi, color: "text-green-500", label: "Connected" },
    disconnected: { icon: WifiOff, color: "text-red-500", label: "Disconnected" },
    checking: { icon: RefreshCw, color: "text-blue-500", label: "Connecting...", spin: true },
    stale: { icon: AlertCircle, color: "text-amber-500", label: "Stale" },
  }[status];

  const Icon = config.icon;

  return (
    <div className="flex items-center gap-2">
      <Icon
        className={cn("h-4 w-4", config.color, "spin" in config && config.spin && "animate-spin")}
      />
      <span className={cn("text-sm font-medium", config.color)}>{config.label}</span>
      {lastSuccessAt && status === "connected" && (
        <span className="flex items-center gap-1 text-xs text-gray-400">
          <Clock className="h-3 w-3" />
          {new Date(lastSuccessAt).toLocaleTimeString()}
        </span>
      )}
    </div>
  );
}

export default function Header() {
  const { baseUrl, isLoading, fetchTopology } = {
    ...useConnectionStore(),
    ...useTopologyStore(),
  };
  const handleRefresh = () => {
    void fetchTopology(baseUrl);
  };

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-6 shadow-sm">
      <StatusIndicator />
      <div className="flex items-center gap-3">
        <span className="text-xs text-gray-400 hidden sm:block truncate max-w-[240px]">
          {baseUrl}
        </span>
        <button
          onClick={handleRefresh}
          disabled={isLoading}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium",
            "bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-60 transition-colors"
          )}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", isLoading && "animate-spin")} />
          Refresh
        </button>
      </div>
    </header>
  );
}
