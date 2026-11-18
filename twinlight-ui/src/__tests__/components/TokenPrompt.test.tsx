import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { useEffect } from "react";
import { withHostMeta } from "../helpers/hostMeta";
import TokenPrompt from "@/components/common/TokenPrompt";
import PageGate from "@/components/layout/PageGate";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { checkConnection } from "@/hooks/useConnectionCheck";
import { createClient, resetRestconfRoots } from "@/api/client";
import { SESSION_TOKEN_KEY, resetMemoryToken } from "@/lib/tokenStorage";

const GOOD = "good-token-0123456789";
const BAD = "bad-token-0123456789";
const BASE = "http://dt";

const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = withHostMeta(mockFetch);

function respond(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const context = {
  "tapi-common:context": {
    "service-interface-point": [{ uuid: "sip-1" }],
    "tapi-topology:topology-context": { topology: [{ uuid: "topo-1", node: [], link: [] }] },
  },
};

/** A twin that requires a token (or, with `open`, has authentication off). */
function twin({ open = false, accepts = GOOD }: { open?: boolean; accepts?: string } = {}) {
  mockFetch.mockImplementation((url: string, init?: RequestInit) => {
    const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
    const authorised = open || auth === `Bearer ${accepts}`;
    if (url.endsWith("/health")) return respond({ status: "ok" });
    if (url.endsWith("/internal/whoami")) {
      return authorised
        ? respond(open ? { principal: null, authentication: "disabled" }
                       : { principal: "ui", authentication: "bearer-token" })
        : respond({ "ietf-restconf:errors": { error: [] } }, 401);
    }
    if (!authorised) return respond({}, 401);
    if (url.endsWith("/restconf/data/tapi-common:context")) return respond(context);
    return respond({});
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetMemoryToken();
  resetRestconfRoots();
  mockFetch.mockReset();
  useConnectionStore.setState({
    baseUrl: BASE,
    apiToken: "",
    status: "disconnected",
    ready: false,
    tokenPromptOpen: false,
    tokenPromptDismissedFor: null,
    authEpoch: 0,
  });
  useTopologyStore.setState({ data: null, fetchedAt: null, error: null, isStale: false });
});

const promptOpen = () => useConnectionStore.getState().tokenPromptOpen;

describe("when the UI asks for a token", () => {
  it("asks when the twin requires one and the session has none", async () => {
    twin();
    await checkConnection();
    expect(useConnectionStore.getState().status).toBe("unauthorized");
    expect(promptOpen()).toBe(true);
    render(<TokenPrompt />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/requires an API token/)).toBeInTheDocument();
  });

  it("never asks when the twin has authentication off", async () => {
    twin({ open: true });
    await checkConnection();
    await checkConnection();
    expect(useConnectionStore.getState().status).toBe("connected");
    expect(promptOpen()).toBe(false);
    render(<TokenPrompt />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not ask again on the next poll once dismissed", async () => {
    twin();
    await checkConnection();
    render(<TokenPrompt />);
    fireEvent.click(screen.getByText("Not now"));
    expect(promptOpen()).toBe(false);
    await checkConnection(); // the next poll tick
    expect(promptOpen()).toBe(false);
    expect(useConnectionStore.getState().status).toBe("unauthorized");
    // A deliberate action (the banner's "Enter token") asks again.
    act(() => useConnectionStore.getState().requestToken({ force: true }));
    expect(promptOpen()).toBe(true);
  });

  it("asks again when a token that worked starts being rejected", async () => {
    twin();
    useConnectionStore.getState().setApiToken(GOOD);
    await checkConnection();
    expect(useConnectionStore.getState().status).toBe("connected");
    expect(promptOpen()).toBe(false);
    twin({ accepts: "rotated-token-0123456789" }); // the twin's tokens changed
    await checkConnection();
    expect(useConnectionStore.getState().status).toBe("unauthorized");
    expect(promptOpen()).toBe(true);
  });

  it("closes on Escape, focuses the field, and hides the token by default", async () => {
    twin();
    await checkConnection();
    render(<TokenPrompt />);
    const input = screen.getByLabelText("API Token");
    expect(document.activeElement).toBe(input);
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
    fireEvent.click(screen.getByLabelText("Show token"));
    expect(input).toHaveAttribute("type", "text");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(promptOpen()).toBe(false);
  });
});

describe("submitting a token", () => {
  async function promptFor() {
    twin();
    await checkConnection();
    render(<TokenPrompt />);
    return screen.getByLabelText("API Token");
  }

  it("refuses a rejected token: says so, keeps nothing, lets the user retry", async () => {
    const input = await promptFor();
    fireEvent.change(input, { target: { value: BAD } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent(/rejected this token/);
    expect(screen.getByRole("alert").textContent).not.toContain(BAD);
    expect(promptOpen()).toBe(true);
    expect(sessionStorage.getItem(SESSION_TOKEN_KEY)).toBeNull();
    expect(useConnectionStore.getState().apiToken).toBe("");
  });

  it("keeps an accepted token for the session and resumes the interrupted load", async () => {
    const input = await promptFor();
    fireEvent.change(input, { target: { value: `  ${GOOD}  ` } });
    fireEvent.submit(input.closest("form")!); // Enter in the field submits the form
    await waitFor(() => expect(promptOpen()).toBe(false));

    expect(sessionStorage.getItem(SESSION_TOKEN_KEY)).toBe(GOOD);
    for (let i = 0; i < localStorage.length; i++) {
      expect(localStorage.getItem(localStorage.key(i)!)).not.toContain(GOOD);
    }
    expect(useConnectionStore.getState().authEpoch).toBe(1);

    // The connection check and the topology load run again, with the token.
    await waitFor(() => expect(useConnectionStore.getState().status).toBe("connected"));
    await waitFor(() => expect(useTopologyStore.getState().data?.sips).toEqual([{ uuid: "sip-1" }]));
    const contextCall = mockFetch.mock.calls.find(([url]) =>
      String(url).endsWith("/restconf/data/tapi-common:context")
    );
    expect(contextCall?.[1].headers.Authorization).toBe(`Bearer ${GOOD}`);

    // Later requests carry it too.
    await createClient(BASE).getPhysicalContext();
    const last = mockFetch.mock.calls[mockFetch.mock.calls.length - 1];
    expect(last[1].headers.Authorization).toBe(`Bearer ${GOOD}`);
  });
});

describe("a store created again in the same session", () => {
  it("picks the token up without asking", async () => {
    sessionStorage.setItem(SESSION_TOKEN_KEY, GOOD);
    vi.resetModules();
    const fresh = await import("@/store/connection");
    const hook = await import("@/hooks/useConnectionCheck");
    expect(fresh.useConnectionStore.getState().apiToken).toBe(GOOD);
    fresh.useConnectionStore.setState({ baseUrl: BASE });
    twin();
    await hook.checkConnection();
    expect(fresh.useConnectionStore.getState().status).toBe("connected");
    expect(fresh.useConnectionStore.getState().tokenPromptOpen).toBe(false);
  });

  it("moves a token an earlier build left in localStorage into the session", async () => {
    localStorage.setItem(
      "twinlight-ui:settings",
      JSON.stringify({ state: { baseUrl: BASE, apiToken: GOOD, pollInterval: 20 }, version: 0 })
    );
    vi.resetModules();
    const fresh = await import("@/store/connection");
    expect(fresh.useConnectionStore.getState().apiToken).toBe(GOOD);
    expect(fresh.useConnectionStore.getState().pollInterval).toBe(20);
    expect(sessionStorage.getItem(SESSION_TOKEN_KEY)).toBe(GOOD);
    expect(localStorage.getItem("twinlight-ui:settings")).not.toContain(GOOD);
    // Saving settings again does not write it back.
    fresh.useConnectionStore.getState().setPollInterval(30);
    expect(localStorage.getItem("twinlight-ui:settings")).not.toContain(GOOD);
  });
});

describe("PageGate", () => {
  let mounts = 0;
  function Page() {
    useEffect(() => {
      mounts += 1;
    }, []);
    return <p>page</p>;
  }

  beforeEach(() => {
    mounts = 0;
  });

  it("holds the pages until the first check settles", () => {
    render(<PageGate><Page /></PageGate>);
    expect(screen.queryByText("page")).not.toBeInTheDocument();
    act(() => useConnectionStore.getState().setReady());
    expect(screen.getByText("page")).toBeInTheDocument();
  });

  it("waits while the prompt is open, then loads the pages afresh", () => {
    useConnectionStore.setState({ ready: true });
    render(<PageGate><Page /></PageGate>);
    expect(mounts).toBe(1);
    act(() => useConnectionStore.getState().requestToken());
    expect(screen.queryByText("page")).not.toBeInTheDocument();
    expect(screen.getByText(/Waiting for an API token/)).toBeInTheDocument();
    act(() => useConnectionStore.getState().acceptToken(GOOD));
    expect(screen.getByText("page")).toBeInTheDocument();
    expect(mounts).toBe(2);
  });

  it("shows the pages again when the prompt is dismissed", () => {
    useConnectionStore.setState({ ready: true, tokenPromptOpen: true });
    render(<PageGate><Page /></PageGate>);
    act(() => useConnectionStore.getState().dismissTokenPrompt());
    expect(screen.getByText("page")).toBeInTheDocument();
  });
});
