import { Fragment, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { useConnectionStore } from "@/store/connection";

/**
 * Holds the pages back until the UI knows whether the twin needs a token.
 *
 * Start-up order: the connection check (/health, /internal/whoami) settles
 * first; if the twin needs a token the prompt asks for it; only then do the
 * pages mount, discover the RESTCONF root and load. When a token is
 * accepted, authEpoch moves on and the pages mount afresh, so a load the
 * prompt interrupted runs again without a manual refresh. A dismissed
 * prompt lets the pages (and Settings) render as before, with the banner.
 */
export default function PageGate({ children }: { children: ReactNode }) {
  const ready = useConnectionStore((s) => s.ready);
  const waitingForToken = useConnectionStore((s) => s.tokenPromptOpen);
  const authEpoch = useConnectionStore((s) => s.authEpoch);

  if (!ready || waitingForToken) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-gray-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        {waitingForToken ? "Waiting for an API token…" : "Connecting to the DT…"}
      </div>
    );
  }
  return <Fragment key={authEpoch}>{children}</Fragment>;
}
