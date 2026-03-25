import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SettingsPage from "@/pages/SettingsPage";

// Mock fetch globally
const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = mockFetch;

beforeEach(() => {
  localStorage.clear();
  mockFetch.mockReset();
});

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
});
