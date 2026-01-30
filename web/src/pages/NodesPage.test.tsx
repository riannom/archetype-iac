import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, MemoryRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "../theme/ThemeProvider";
import NodesPage from "./NodesPage";
import {
  createMockDeviceModel,
  createMockImageEntry,
  resetFactories,
} from "../test-utils/factories";

// Mock fetch globally
const mockFetch = vi.fn();
const originalFetch = globalThis.fetch;

// Mock useUser hook with regular user
vi.mock("../contexts/UserContext", () => ({
  useUser: () => ({
    user: {
      id: "user-1",
      email: "user@example.com",
      is_admin: false,
      is_active: true,
    },
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

function renderNodesPage(initialPath = "/nodes/devices") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <ThemeProvider>
        <Routes>
          <Route path="/nodes/*" element={<NodesPage />} />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>
  );
}

function renderNodesPageWithBrowser() {
  return render(
    <BrowserRouter>
      <ThemeProvider>
        <NodesPage />
      </ThemeProvider>
    </BrowserRouter>
  );
}

describe("NodesPage", () => {
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
    it("shows loading spinner while fetching", () => {
      mockFetch.mockReturnValue(new Promise(() => {})); // Never resolves

      renderNodesPageWithBrowser();

      expect(screen.getByText("Loading...")).toBeInTheDocument();
    });
  });

  describe("tabs", () => {
    it("renders all tabs", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Device Management")).toBeInTheDocument();
        expect(screen.getByText("Image Management")).toBeInTheDocument();
        expect(screen.getByText("Sync Jobs")).toBeInTheDocument();
      });
    });

    it("shows devices tab by default", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPage("/nodes/devices");

      await waitFor(() => {
        const deviceTab = screen.getByText("Device Management");
        expect(deviceTab).toHaveClass("text-sage-600");
      });
    });

    it("switches to images tab when clicked", async () => {
      const user = userEvent.setup();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Image Management")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Image Management"));

      expect(mockNavigate).toHaveBeenCalledWith("/nodes/images");
    });

    it("switches to sync tab when clicked", async () => {
      const user = userEvent.setup();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Sync Jobs")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Sync Jobs"));

      expect(mockNavigate).toHaveBeenCalledWith("/nodes/sync");
    });
  });

  describe("header", () => {
    it("displays brand name", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });

    it("displays page subtitle", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Node Management")).toBeInTheDocument();
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
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Back")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Back"));

      expect(mockNavigate).toHaveBeenCalledWith("/");
    });
  });

  describe("refresh", () => {
    it("refreshes data when refresh button clicked", async () => {
      const user = userEvent.setup();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Refresh")).toBeInTheDocument();
      });

      // Mock refresh responses
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      await user.click(screen.getByText("Refresh"));

      await waitFor(() => {
        // Initial 3 calls + 3 refresh calls
        expect(mockFetch).toHaveBeenCalledTimes(6);
      });
    });
  });

  describe("theme controls", () => {
    it("renders theme toggle button", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        const themeButton = document.querySelector('button[title*="Switch to"]');
        expect(themeButton).toBeInTheDocument();
      });
    });

    it("renders theme selector button", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      await waitFor(() => {
        const paletteButton = document.querySelector('button[title="Theme Settings"]');
        expect(paletteButton).toBeInTheDocument();
      });
    });
  });

  describe("sync jobs tab", () => {
    it("shows sync jobs title when on sync tab", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPage("/nodes/sync");

      await waitFor(() => {
        expect(screen.getByText("Image Sync Jobs")).toBeInTheDocument();
      });
    });

    it("shows sync jobs description", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPage("/nodes/sync");

      await waitFor(() => {
        expect(
          screen.getByText("Track image synchronization progress across agents")
        ).toBeInTheDocument();
      });
    });
  });

  describe("custom devices persistence", () => {
    it("loads custom devices from localStorage", async () => {
      const customDevices = [{ id: "custom-1", label: "My Custom Device" }];
      localStorage.setItem(
        "archetype_custom_devices",
        JSON.stringify(customDevices)
      );

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      // The custom device should be loaded (though we can't easily verify internal state)
      await waitFor(() => {
        expect(screen.getByText("Device Management")).toBeInTheDocument();
      });
    });

    it("handles invalid localStorage data gracefully", async () => {
      localStorage.setItem("archetype_custom_devices", "invalid json");

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPageWithBrowser();

      // Should not throw, page should render
      await waitFor(() => {
        expect(screen.getByText("Device Management")).toBeInTheDocument();
      });
    });
  });

  describe("URL-based tab state", () => {
    it("shows devices tab for /nodes/devices path", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPage("/nodes/devices");

      await waitFor(() => {
        const tab = screen.getByText("Device Management");
        expect(tab).toHaveClass("text-sage-600");
      });
    });

    it("shows images tab for /nodes/images path", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPage("/nodes/images");

      await waitFor(() => {
        const tab = screen.getByText("Image Management");
        expect(tab).toHaveClass("text-sage-600");
      });
    });

    it("shows sync tab for /nodes/sync path", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve([]),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: {} }),
      });
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ images: [] }),
      });

      renderNodesPage("/nodes/sync");

      await waitFor(() => {
        const tab = screen.getByText("Sync Jobs");
        expect(tab).toHaveClass("text-sage-600");
      });
    });
  });
});
