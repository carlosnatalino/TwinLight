import { useEffect, useId, useRef, useState } from "react";
import { Eye, EyeOff, KeyRound, Loader2, X, XCircle } from "lucide-react";
import { createClient, isAuthError } from "@/api/client";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { checkConnection } from "@/hooks/useConnectionCheck";

/**
 * Asks for the API token when the twin needs one (see checkConnection).
 *
 * The token is checked against /internal/whoami before it is accepted; an
 * accepted token is kept for this browser session and every page that was
 * waiting for it loads. Dismissing leaves the banner and Settings to come
 * back to it later.
 */
export default function TokenPrompt() {
  const { baseUrl, apiToken, tokenPromptOpen, dismissTokenPrompt, acceptToken } =
    useConnectionStore();
  const fetchTopology = useTopologyStore((s) => s.fetchTopology);

  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!tokenPromptOpen) return;
    setTokenInput("");
    setError("");
    setShowToken(false);
    inputRef.current?.focus();
  }, [tokenPromptOpen]);

  if (!tokenPromptOpen) return null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const token = tokenInput.trim();
    if (!token || checking) return;
    setChecking(true);
    setError("");
    try {
      // Check the token as typed, before anything keeps it.
      await createClient(baseUrl, token).whoami();
    } catch (err) {
      setChecking(false);
      // Never echo the token: the messages name the outcome only.
      setError(
        isAuthError(err)
          ? "The DT rejected this token. Check it and try again."
          : "Could not check the token: the DT did not answer."
      );
      inputRef.current?.focus();
      return;
    }
    setChecking(false);
    acceptToken(token);
    // Continue what was waiting for the token: the status, the shared
    // topology (pages remount through authEpoch and reload their own data).
    void checkConnection();
    void fetchTopology(baseUrl);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          dismissTokenPrompt();
        }
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-6 shadow-lg"
      >
        <div className="flex items-start gap-3">
          <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
          <div className="flex-1">
            <h2 id={titleId} className="text-base font-semibold text-gray-900">
              API token required
            </h2>
            <p className="mt-1 text-sm text-gray-600">
              {apiToken
                ? `The DT at ${baseUrl} rejected the API token in use. Enter another one.`
                : `The DT at ${baseUrl} requires an API token.`}
            </p>
          </div>
          <button
            type="button"
            onClick={dismissTokenPrompt}
            aria-label="Dismiss"
            className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-3">
          <label htmlFor="token-prompt-input" className="block text-sm font-medium text-gray-700">
            API Token
          </label>
          <div className="flex gap-2">
            <input
              id="token-prompt-input"
              ref={inputRef}
              type={showToken ? "text" : "password"}
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <button
              type="button"
              onClick={() => setShowToken((v) => !v)}
              aria-label={showToken ? "Hide token" : "Show token"}
              className="rounded-md border border-gray-300 bg-white px-3 py-2 text-gray-500 hover:bg-gray-50 transition-colors"
            >
              {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          {error && (
            <p role="alert" className="flex items-center gap-1 text-sm text-red-600">
              <XCircle className="h-4 w-4 shrink-0" />
              {error}
            </p>
          )}
          <p className="text-xs text-gray-400">
            Kept in this browser tab&apos;s session storage until the tab or browser is
            closed, and sent as{" "}
            <code className="bg-gray-100 px-1 rounded">Authorization: Bearer …</code>.
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={dismissTokenPrompt}
              className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Not now
            </button>
            <button
              type="submit"
              disabled={!tokenInput.trim() || checking}
              className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {checking && <Loader2 className="h-4 w-4 animate-spin" />}
              Use token
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
