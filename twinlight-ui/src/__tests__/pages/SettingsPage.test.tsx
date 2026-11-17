import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SettingsPage from "@/pages/SettingsPage";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";

// Mock fetch globally
const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = mockFetch;

beforeEach(() => {
  localStorage.clear();
  mockFetch.mockReset();
  useConnectionStore.setState({ apiToken: "", baseUrl: "http://localhost:8080" });
});

function respond(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

function renderSettings() {
  return render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>
  );
}

describe("SettingsPage", () => {
  it("renders the URL input", () => {
    renderSettings();
    expect(screen.getByPlaceholderText("http://localhost:8080")).toBeInTheDocument();
  });

  it("renders the poll interval slider", () => {
    renderSettings();
    const slider = screen.getByRole("slider");
    expect(slider).toBeInTheDocument();
    expect(slider).toHaveAttribute("min", "5");
    expect(slider).toHaveAttribute("max", "60");
  });

  it("renders Test Connection button", () => {
    renderSettings();
    expect(screen.getByText("Test Connection")).toBeInTheDocument();
  });

  it("renders cache management buttons", () => {
    renderSettings();
    expect(screen.getByText("Clear All Cache")).toBeInTheDocument();
    expect(screen.getByText("Clear Monitoring History")).toBeInTheDocument();
  });

  it("shows success message after successful connection test", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: "ok" }),
      text: () => Promise.resolve(""),
    });

    renderSettings();
    fireEvent.click(screen.getByText("Test Connection"));

    const successMsg = await screen.findByText(/Connected/i);
    expect(successMsg).toBeInTheDocument();
  });

  it("shows error message on failed connection test", async () => {
    mockFetch.mockRejectedValueOnce(new Error("Connection refused"));
    renderSettings();
    fireEvent.click(screen.getByText("Test Connection"));
    const errMsg = await screen.findByText(/Connection refused/i);
    expect(errMsg).toBeInTheDocument();
  });

  it("saves URL when Save button is clicked", () => {
    renderSettings();
    const input = screen.getByPlaceholderText("http://localhost:8080");
    fireEvent.change(input, { target: { value: "http://192.168.1.100:8080" } });
    fireEvent.click(screen.getByText("Save"));
    // Verify no error thrown (zustand store update is synchronous)
    expect(input).toHaveValue("http://192.168.1.100:8080");
  });

  it("renders the API token as a masked input", () => {
    renderSettings();
    expect(screen.getByLabelText("API Token")).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByLabelText("Show token"));
    expect(screen.getByLabelText("API Token")).toHaveAttribute("type", "text");
  });

  it("saves the token, re-checks at once and refetches with it", async () => {
    mockFetch.mockImplementation((url: string) =>
      Promise.resolve(
        url.endsWith("/health")
          ? respond({ status: "ok" })
          : url.endsWith("/internal/whoami")
            ? respond({ principal: "ui", authentication: "bearer-token" })
            : respond({ "tapi-common:context": {} })
      )
    );
    useConnectionStore.setState({ status: "unauthorized" });
    renderSettings();
    fireEvent.change(screen.getByLabelText("API Token"), {
      target: { value: "  tok-abc  " },
    });
    fireEvent.click(screen.getByText("Save token"));
    expect(useConnectionStore.getState().apiToken).toBe("tok-abc");
    expect(screen.getByText("API token saved.")).toBeInTheDocument();

    // No waiting for the next poll: the status follows the new token now.
    await waitFor(() => expect(useConnectionStore.getState().status).toBe("connected"));
    await waitFor(() => expect(useTopologyStore.getState().isLoading).toBe(false));
    const contextCall = mockFetch.mock.calls.find(([url]) =>
      String(url).endsWith("/data/tapi-common:context")
    );
    expect(contextCall?.[1].headers.Authorization).toBe("Bearer tok-abc");
  });

  it("clears a previous test result when saving", async () => {
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({ principal: null, authentication: "disabled" }));
    renderSettings();
    fireEvent.click(screen.getByText("Test Connection"));
    await screen.findByText(/authentication is disabled/);
    mockFetch.mockImplementation(() => Promise.reject(new TypeError("offline")));
    fireEvent.click(screen.getByText("Save token"));
    expect(screen.queryByText(/authentication is disabled/)).not.toBeInTheDocument();
    await waitFor(() => expect(useTopologyStore.getState().isLoading).toBe(false));
  });

  it("tests the typed token before it is saved", async () => {
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({ principal: "ui", authentication: "bearer-token" }));
    renderSettings();
    fireEvent.change(screen.getByLabelText("API Token"), { target: { value: "tok-abc" } });
    fireEvent.click(screen.getByText("Test Connection"));

    expect(await screen.findByText(/authenticated as "ui"/)).toBeInTheDocument();
    const whoamiCall = mockFetch.mock.calls[1];
    expect(whoamiCall[0]).toBe("http://localhost:8080/internal/whoami");
    expect(whoamiCall[1].headers.Authorization).toBe("Bearer tok-abc");
    expect(useConnectionStore.getState().apiToken).toBe("");
  });

  it("reports a rejected token", async () => {
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({}, 401));
    renderSettings();
    fireEvent.change(screen.getByLabelText("API Token"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByText("Test Connection"));
    expect(await screen.findByText(/rejected this API token/)).toBeInTheDocument();
  });

  it("reports a missing token", async () => {
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({}, 401));
    renderSettings();
    fireEvent.click(screen.getByText("Test Connection"));
    expect(await screen.findByText(/requires an API token/)).toBeInTheDocument();
    expect(useConnectionStore.getState().status).toBe("unauthorized");
  });

  it("says when the DT has authentication disabled", async () => {
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({ principal: null, authentication: "disabled" }));
    renderSettings();
    fireEvent.click(screen.getByText("Test Connection"));
    expect(await screen.findByText(/authentication is disabled/)).toBeInTheDocument();
  });
});
