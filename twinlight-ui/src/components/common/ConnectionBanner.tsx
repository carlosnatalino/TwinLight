import { useConnectionStore } from "@/store/connection";
import { AlertTriangle, WifiOff } from "lucide-react";
import { useNavigate } from "react-router-dom";

export default function ConnectionBanner() {
  const { status, baseUrl } = useConnectionStore();
  const navigate = useNavigate();

  if (status === "connected" || status === "checking") return null;

  const isStale = status === "stale";

  return (
    <div
      className={`flex items-center gap-2 px-4 py-2 text-sm font-medium ${
        isStale ? "bg-amber-50 text-amber-800 border-b border-amber-200" : "bg-red-50 text-red-800 border-b border-red-200"
      }`}
    >
      {isStale ? (
        <AlertTriangle className="h-4 w-4 shrink-0" />
      ) : (
        <WifiOff className="h-4 w-4 shrink-0" />
      )}
      <span>
        {isStale
          ? "Showing cached topology data — DT unreachable"
          : !baseUrl
            ? "No DT URL configured."
            : `Cannot reach DT at ${baseUrl}.`}
      </span>
      <button
        onClick={() => navigate("/settings")}
        className="ml-auto underline hover:no-underline"
      >
        Settings
      </button>
    </div>
  );
}
