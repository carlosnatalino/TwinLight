import { useState } from "react";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { createClient } from "@/api/client";
import { cachePurgeByPrefix, cacheUsageBytes } from "@/lib/cache";
import { clearAllSeries } from "@/lib/time-series";
import { CheckCircle, XCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

type TestResult = "idle" | "testing" | "ok" | "error";

export default function SettingsPage() {
  const { baseUrl, pollInterval, setBaseUrl, setPollInterval, setStatus, setLastSuccess } = useConnectionStore();
  const { clearCache } = useTopologyStore();

  const [urlInput, setUrlInput] = useState(baseUrl);
  const [testResult, setTestResult] = useState<TestResult>("idle");
  const [testMessage, setTestMessage] = useState("");
  const [clearMessage, setClearMessage] = useState("");

  const storageBytes = cacheUsageBytes("tapi-twin-ui:");
  const storageKb = (storageBytes / 1024).toFixed(1);

  async function handleTestConnection() {
    const url = urlInput.trim();
    if (!url) return;
    setTestResult("testing");
    setTestMessage("");
    try {
      const client = createClient(url);
      const result = await client.health();
      setTestResult("ok");
      setTestMessage(`Connected. Status: ${result.status}`);
      if (url === baseUrl) setLastSuccess();
    } catch (err) {
      setTestResult("error");
      setTestMessage(err instanceof Error ? err.message : "Connection failed");
      if (url === baseUrl) setStatus("disconnected");
    }
  }

  function handleSaveUrl() {
    setBaseUrl(urlInput.trim());
    setTestResult("idle");
  }

  function handleClearAll() {
    cachePurgeByPrefix("tapi-twin-ui:");
    clearCache();
    setClearMessage("All cache cleared.");
    setTimeout(() => setClearMessage(""), 3000);
  }

  function handleClearMonitoring() {
    clearAllSeries();
    setClearMessage("Monitoring history cleared.");
    setTimeout(() => setClearMessage(""), 3000);
  }

  return (
    <div className="mx-auto max-w-2xl p-6 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-500">Configure the connection to your Digital Twin.</p>
      </div>

      {/* DT Connection */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-5">
        <h2 className="text-base font-semibold text-gray-800">Digital Twin Connection</h2>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">DT Base URL</label>
          <div className="flex gap-2">
            <input
              type="url"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder="http://localhost:8080"
              className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <button
              onClick={handleSaveUrl}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
            >
              Save
            </button>
          </div>
          <p className="mt-1.5 text-xs text-gray-400">
            Make sure the DT has CORS configured to allow requests from this origin.
          </p>
        </div>

        <div>
          <button
            onClick={handleTestConnection}
            disabled={testResult === "testing"}
            className={cn(
              "inline-flex items-center gap-2 rounded-md border px-4 py-2 text-sm font-medium transition-colors",
              testResult === "testing"
                ? "border-gray-200 bg-gray-50 text-gray-400"
                : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
            )}
          >
            {testResult === "testing" && <Loader2 className="h-4 w-4 animate-spin" />}
            {testResult === "ok" && <CheckCircle className="h-4 w-4 text-green-500" />}
            {testResult === "error" && <XCircle className="h-4 w-4 text-red-500" />}
            Test Connection
          </button>
          {testMessage && (
            <p
              className={cn(
                "mt-2 text-sm",
                testResult === "ok" ? "text-green-600" : "text-red-600"
              )}
            >
              {testMessage}
            </p>
          )}
        </div>
      </section>

      {/* Poll Interval */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
        <h2 className="text-base font-semibold text-gray-800">Monitoring Poll Interval</h2>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Poll interval: <span className="font-bold text-blue-600">{pollInterval}s</span>
          </label>
          <input
            type="range"
            min={5}
            max={60}
            step={5}
            value={pollInterval}
            onChange={(e) => setPollInterval(Number(e.target.value))}
            className="w-full accent-blue-600"
          />
          <div className="flex justify-between text-xs text-gray-400 mt-1">
            <span>5s</span>
            <span>60s</span>
          </div>
        </div>
      </section>

      {/* Cache management */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-800">Cache Management</h2>
          <span className="text-xs text-gray-400">
            Used: {storageKb} KB / ~5 MB
          </span>
        </div>

        <div className="flex flex-wrap gap-3">
          <button
            onClick={handleClearAll}
            className="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-100 transition-colors"
          >
            Clear All Cache
          </button>
          <button
            onClick={handleClearMonitoring}
            className="rounded-md border border-orange-200 bg-orange-50 px-4 py-2 text-sm font-medium text-orange-700 hover:bg-orange-100 transition-colors"
          >
            Clear Monitoring History
          </button>
        </div>

        {clearMessage && (
          <p className="text-sm text-green-600 flex items-center gap-1">
            <CheckCircle className="h-4 w-4" />
            {clearMessage}
          </p>
        )}

        <div className="rounded-md bg-gray-50 border border-gray-200 p-3 text-xs text-gray-600">
          <p className="font-medium mb-1">CORS Configuration</p>
          <p>
            Your DT must include this UI&apos;s origin in its CORS configuration. In{" "}
            <code className="bg-gray-200 px-1 rounded">twin_config.yaml</code>:
          </p>
          <pre className="mt-1 text-xs bg-gray-100 p-2 rounded overflow-x-auto">
            {`server:\n  cors_origins:\n    - "${typeof window !== "undefined" ? window.location.origin : "http://localhost:5173"}"`}
          </pre>
        </div>
      </section>
    </div>
  );
}
