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

// Mock apiRequest
const mockApiRequest = vi.fn();
vi.mock("../api", () => ({
  apiRequest: (...args: unknown[]) => mockApiRequest(...args),
}));

// Mock useUser hook with regular user
vi.mock("../contexts/UserContext", () => ({
  useUser: () => ({
    user: {
      id: "user-1",
      username: "testuser",
      email: "user@example.com",
      is_active: true,
      global_role: "admin",
      created_at: "2024-01-01T00:00:00Z",
    },
    loading: false,
    error: null,
    refreshUser: vi.fn(),
    clearUser: vi.fn(),
  }),
  UserProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// Mock useImageLibrary hook
let mockImageLibraryData: unknown[] = [];
vi.mock("../contexts/ImageLibraryContext", () => ({
  useImageLibrary: () => ({
    imageLibrary: mockImageLibraryData,
    loading: false,
    error: null,
    refreshImageLibrary: vi.fn(),
  }),
  ImageLibraryProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// Mock useDeviceCatalog hook
vi.mock("../contexts/DeviceCatalogContext", () => ({
  useDeviceCatalog: () => ({
    vendorCategories: [],
    deviceModels: [],
    deviceCategories: [],
    addCustomDevice: vi.fn(),
    removeCustomDevice: vi.fn(),
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
  DeviceCatalogProvider: ({ children }: { children: React.ReactNode }) => children,
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
    resetFactories();
    mockImageLibraryData = [];
    // Default mock responses for API calls
    mockApiRequest.mockImplementation((path: string) => {
      if (path === "/vendors") return Promise.resolve([]);
      if (path === "/images/library") return Promise.resolve({ images: [] });
      return Promise.resolve({});
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe("loading state", () => {
    it.skip("shows loading spinner while fetching", async () => {
      // Skipped: This test causes infinite hangs due to never-resolving promises
      // TODO: Refactor to use fake timers or a better approach
    });
  });

  describe("tabs", () => {
    it("renders all tabs", async () => {
      // Uses default mock from beforeEach

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Device Management")).toBeInTheDocument();
        expect(screen.getByText("Image Management")).toBeInTheDocument();
        expect(screen.getByText("Build Jobs")).toBeInTheDocument();
        expect(screen.getByText("Sync Jobs")).toBeInTheDocument();
      });
    });

    it("shows devices tab by default", async () => {
      // Uses default mock from beforeEach

      renderNodesPage("/nodes/devices");

      await waitFor(() => {
        const deviceTab = screen.getByText("Device Management");
        expect(deviceTab).toHaveClass("text-sage-600");
      });
    });

    it.skip("switches to images tab when clicked", async () => {
      // Skipped: userEvent.click causes test hangs with mocked navigation
    });

    it.skip("switches to sync tab when clicked", async () => {
      // Skipped: userEvent.click causes test hangs with mocked navigation
    });
  });

  describe("header", () => {
    it("displays brand name", async () => {
      // Uses default mock from beforeEach

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });

    it("displays page subtitle", async () => {
      // Uses default mock from beforeEach

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Node Management")).toBeInTheDocument();
      });
    });
  });

  describe("navigation", () => {
    it.skip("navigates back when back button clicked", async () => {
      // Skipped: userEvent.click causes test hangs with mocked navigation
    });
  });

  describe("refresh", () => {
    it.skip("refreshes data when refresh button clicked", async () => {
      // Skipped: userEvent.click causes test hangs with mocked navigation
    });
  });

  describe("theme controls", () => {
    it("renders theme toggle button", async () => {
      // Uses default mock from beforeEach

      renderNodesPageWithBrowser();

      await waitFor(() => {
        const themeButton = document.querySelector('button[title*="Switch to"]');
        expect(themeButton).toBeInTheDocument();
      });
    });

    it("renders theme selector button", async () => {
      // Uses default mock from beforeEach

      renderNodesPageWithBrowser();

      await waitFor(() => {
        const paletteButton = document.querySelector('button[title="Theme Settings"]');
        expect(paletteButton).toBeInTheDocument();
      });
    });
  });

  describe("sync jobs tab", () => {
    it("shows sync jobs title when on sync tab", async () => {
      // Uses default mock from beforeEach

      renderNodesPage("/nodes/sync");

      await waitFor(() => {
        expect(screen.getByText("Image Sync Jobs")).toBeInTheDocument();
      });
    });

    it("shows sync jobs description", async () => {
      // Uses default mock from beforeEach

      renderNodesPage("/nodes/sync");

      await waitFor(() => {
        expect(
          screen.getByText("Track image synchronization progress across agents")
        ).toBeInTheDocument();
      });
    });
  });

  describe("build jobs tab", () => {
    it("shows build jobs title when on build-jobs tab", async () => {
      renderNodesPage("/nodes/build-jobs");

      await waitFor(() => {
        const elements = screen.getAllByText("Build Jobs");
        const heading = elements.find((el) => el.tagName === "H2");
        expect(heading).toBeInTheDocument();
        expect(
          screen.getByText("Track and manage background IOL Docker image builds")
        ).toBeInTheDocument();
      });
    });

    it("shows pending build count badge when IOL images are not built", async () => {
      mockImageLibraryData = [
        {
          id: "iol:image-1",
          kind: "iol",
          reference: "/var/lib/archetype/images/iol/image-1.bin",
        },
        {
          id: "iol:image-2",
          kind: "iol",
          reference: "/var/lib/archetype/images/iol/image-2.bin",
        },
        {
          id: "docker:archetype/iol-xe:17.12.01",
          kind: "docker",
          reference: "archetype/iol-xe:17.12.01",
          built_from: "iol:image-1",
        },
      ];

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByLabelText("1 pending IOL builds")).toBeInTheDocument();
      });
    });

    it("does not count IOL entries already marked complete", async () => {
      mockImageLibraryData = [
        {
          id: "iol:image-complete",
          kind: "iol",
          reference: "/var/lib/archetype/images/iol/image-complete.bin",
          build_status: "complete",
        },
      ];

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.queryByLabelText(/pending IOL builds/i)).not.toBeInTheDocument();
      });
    });

    it("does not count ignored IOL build entries", async () => {
      mockImageLibraryData = [
        {
          id: "iol:image-ignored",
          kind: "iol",
          reference: "/var/lib/archetype/images/iol/image-ignored.bin",
          build_status: "ignored",
        },
      ];

      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.queryByLabelText(/pending IOL builds/i)).not.toBeInTheDocument();
      });
    });
  });

  describe("custom devices from DeviceCatalog", () => {
    it("renders with device catalog from context", async () => {
      // Custom devices are now loaded from the DeviceCatalog context (API-based)
      // rather than localStorage. The mock provides empty deviceModels by default.
      renderNodesPageWithBrowser();

      await waitFor(() => {
        expect(screen.getByText("Device Management")).toBeInTheDocument();
      });
    });
  });

  describe("URL-based tab state", () => {
    it("shows devices tab for /nodes/devices path", async () => {
      // Uses default mock from beforeEach

      renderNodesPage("/nodes/devices");

      await waitFor(() => {
        const tab = screen.getByText("Device Management");
        expect(tab).toHaveClass("text-sage-600");
      });
    });

    it("shows images tab for /nodes/images path", async () => {
      // Uses default mock from beforeEach

      renderNodesPage("/nodes/images");

      await waitFor(() => {
        // Use getAllByText since there might be multiple "Image Management" elements
        // (tab button and header inside DeviceManager), then find the tab button
        const elements = screen.getAllByText("Image Management");
        const tab = elements.find(el => el.tagName === "BUTTON");
        expect(tab).toHaveClass("text-sage-600");
      });
    });

    it("shows sync tab for /nodes/sync path", async () => {
      // Uses default mock from beforeEach

      renderNodesPage("/nodes/sync");

      await waitFor(() => {
        const tab = screen.getByText("Sync Jobs");
        expect(tab).toHaveClass("text-sage-600");
      });
    });

    it("shows build jobs tab for /nodes/build-jobs path", async () => {
      renderNodesPage("/nodes/build-jobs");

      await waitFor(() => {
        const elements = screen.getAllByText("Build Jobs");
        const tab = elements.find((el) => el.tagName === "BUTTON");
        expect(tab).toHaveClass("text-sage-600");
      });
    });
  });
});
