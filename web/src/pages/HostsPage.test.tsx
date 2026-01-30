import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "../theme/ThemeProvider";
import HostsPage from "./HostsPage";
import {
  createMockHost,
  createOnlineHost,
  createOfflineHost,
  createMockLab,
  resetFactories,
  MockHostDetailed,
} from "../test-utils/factories";

// Mock fetch globally
const mockFetch = vi.fn();
const originalFetch = globalThis.fetch;

// Mock useUser hook
const mockUser = {
  id: "admin-1",
  email: "admin@example.com",
  is_admin: true,
  is_active: true,
};

vi.mock("../contexts/UserContext", () => ({
  useUser: () => ({
    user: mockUser,
    loading: false,
    error: null,
    refreshUser: vi.fn(),
    clearUser: vi.fn(),
  }),
  UserProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function renderHostsPage() {
  return render(
    <BrowserRouter>
      <ThemeProvider>
        <HostsPage />
      </ThemeProvider>
    </BrowserRouter>
  );
}

describe("HostsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    globalThis.fetch = mockFetch;
    resetFactories();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  describe("loading state", () => {
    it("shows loading spinner while fetching hosts", () => {
      mockFetch.mockReturnValue(new Promise(() => {})); // Never resolves

      renderHostsPage();

      expect(screen.getByText("Loading hosts...")).toBeInTheDocument();
    });
  });

  describe("empty state", () => {
    it("shows empty state when no hosts registered", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      // Second call for latest version
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("No Hosts Registered")).toBeInTheDocument();
      });
    });
  });

  describe("host display", () => {
    it("displays host information", async () => {
      const host = createOnlineHost({
        name: "Test Agent",
        address: "agent.local:8080",
        version: "1.0.0",
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Test Agent")).toBeInTheDocument();
        expect(screen.getByText("agent.local:8080")).toBeInTheDocument();
      });
    });

    it("displays multiple hosts", async () => {
      const hosts = [
        createOnlineHost({ name: "Agent 1" }),
        createOnlineHost({ name: "Agent 2" }),
        createOfflineHost({ name: "Agent 3" }),
      ];

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(hosts),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Agent 1")).toBeInTheDocument();
        expect(screen.getByText("Agent 2")).toBeInTheDocument();
        expect(screen.getByText("Agent 3")).toBeInTheDocument();
      });
    });

    it("shows online/offline counts", async () => {
      const hosts = [
        createOnlineHost({ name: "Online 1" }),
        createOnlineHost({ name: "Online 2" }),
        createOfflineHost({ name: "Offline 1" }),
      ];

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(hosts),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("2 Online")).toBeInTheDocument();
        expect(screen.getByText("1 Offline")).toBeInTheDocument();
      });
    });
  });

  describe("resource usage bars", () => {
    it("displays CPU usage", async () => {
      const host = createOnlineHost({
        resource_usage: {
          cpu_percent: 75,
          memory_percent: 50,
          memory_used_gb: 8,
          memory_total_gb: 16,
          storage_percent: 60,
          storage_used_gb: 120,
          storage_total_gb: 200,
          containers_running: 5,
          containers_total: 10,
        },
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("CPU")).toBeInTheDocument();
        expect(screen.getByText("75%")).toBeInTheDocument();
      });
    });

    it("displays memory usage", async () => {
      const host = createOnlineHost({
        resource_usage: {
          cpu_percent: 25,
          memory_percent: 50,
          memory_used_gb: 8,
          memory_total_gb: 16,
          storage_percent: 60,
          storage_used_gb: 120,
          storage_total_gb: 200,
          containers_running: 5,
          containers_total: 10,
        },
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Memory")).toBeInTheDocument();
      });
    });

    it("displays storage usage", async () => {
      const host = createOnlineHost();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Storage")).toBeInTheDocument();
      });
    });
  });

  describe("labs display", () => {
    it("shows lab count when labs exist", async () => {
      const host = createOnlineHost({
        labs: [
          createMockLab({ name: "Lab 1", state: "running" }),
          createMockLab({ name: "Lab 2", state: "stopped" }),
        ],
        lab_count: 2,
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("2 Labs")).toBeInTheDocument();
      });
    });

    it("shows singular 'Lab' for single lab", async () => {
      const host = createOnlineHost({
        labs: [createMockLab({ name: "Single Lab" })],
        lab_count: 1,
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("1 Lab")).toBeInTheDocument();
      });
    });
  });

  describe("sync strategy", () => {
    it("displays sync strategy dropdown", async () => {
      const host = createOnlineHost({
        image_sync_strategy: "on_demand",
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Image Sync")).toBeInTheDocument();
      });
    });

    it("updates sync strategy when changed", async () => {
      const user = userEvent.setup();
      const host = createOnlineHost({
        id: "host-1",
        image_sync_strategy: "on_demand",
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Image Sync")).toBeInTheDocument();
      });

      // Mock the PUT request for strategy update
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({}),
      });

      const select = screen.getByDisplayValue("On Demand");
      await user.selectOptions(select, "push");

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining("/agents/host-1/sync-strategy"),
          expect.any(Object)
        );
      });
    });
  });

  describe("update functionality", () => {
    it("shows update available indicator", async () => {
      const host = createOnlineHost({
        version: "1.0.0",
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([host]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "2.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText(/Update to v2.0.0/)).toBeInTheDocument();
      });
    });

    it("shows bulk update button when agents are outdated", async () => {
      const hosts = [
        createOnlineHost({ version: "1.0.0" }),
        createOnlineHost({ version: "1.0.0" }),
      ];

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(hosts),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "2.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText(/Update 2 Agents/)).toBeInTheDocument();
      });
    });
  });

  describe("navigation", () => {
    it("navigates back when back button clicked", async () => {
      const user = userEvent.setup();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Back")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Back"));

      expect(mockNavigate).toHaveBeenCalledWith("/");
    });
  });

  describe("refresh", () => {
    it("refreshes hosts when refresh button clicked", async () => {
      const user = userEvent.setup();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Refresh")).toBeInTheDocument();
      });

      // Mock refresh response
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([createOnlineHost({ name: "New Agent" })]),
      });

      await user.click(screen.getByText("Refresh"));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledTimes(3);
      });
    });
  });

  describe("error handling", () => {
    it("displays error message when fetch fails", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        text: () => Promise.resolve("Server error"),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Server error")).toBeInTheDocument();
      });
    });
  });

  describe("header", () => {
    it("displays page title", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("Compute Hosts")).toBeInTheDocument();
      });
    });

    it("displays brand name", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });
  });

  describe("theme", () => {
    it("renders theme toggle button", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ version: "1.0.0" }),
      });

      renderHostsPage();

      // Check for sun/moon icon for theme toggle
      const themeButton = document.querySelector('button[title*="Switch to"]');
      expect(themeButton).toBeInTheDocument();
    });
  });
});
