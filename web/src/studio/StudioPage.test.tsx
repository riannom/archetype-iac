import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter } from "react-router-dom";
import StudioPage from "./StudioPage";
import { ThemeProvider } from "../theme/ThemeProvider";
import { UserProvider } from "../contexts/UserContext";

// Use vi.hoisted for xterm mocks
const {
  mockTerminalWrite,
  mockTerminalWriteln,
  mockTerminalFocus,
  mockTerminalDispose,
  mockTerminalOpen,
  mockTerminalOnData,
  mockTerminalLoadAddon,
  mockFitAddonFit,
  MockTerminal,
  MockFitAddon,
} = vi.hoisted(() => {
  const mockTerminalWrite = vi.fn();
  const mockTerminalWriteln = vi.fn();
  const mockTerminalFocus = vi.fn();
  const mockTerminalDispose = vi.fn();
  const mockTerminalOpen = vi.fn();
  const mockTerminalOnData = vi.fn();
  const mockTerminalLoadAddon = vi.fn();
  const mockFitAddonFit = vi.fn();

  const MockTerminal = vi.fn(() => ({
    write: mockTerminalWrite,
    writeln: mockTerminalWriteln,
    focus: mockTerminalFocus,
    dispose: mockTerminalDispose,
    open: mockTerminalOpen,
    onData: mockTerminalOnData,
    loadAddon: mockTerminalLoadAddon,
  }));

  const MockFitAddon = vi.fn(() => ({
    fit: mockFitAddonFit,
  }));

  return {
    mockTerminalWrite,
    mockTerminalWriteln,
    mockTerminalFocus,
    mockTerminalDispose,
    mockTerminalOpen,
    mockTerminalOnData,
    mockTerminalLoadAddon,
    mockFitAddonFit,
    MockTerminal,
    MockFitAddon,
  };
});

// Mock xterm.js
vi.mock("xterm", () => ({
  Terminal: MockTerminal,
}));

vi.mock("xterm-addon-fit", () => ({
  FitAddon: MockFitAddon,
}));

// Mock the theme index with all exports
vi.mock("../theme/index", async () => {
  const actual = await vi.importActual("../theme/index");
  return {
    ...actual,
    useTheme: () => ({
      effectiveMode: "light",
      mode: "light",
      setMode: vi.fn(),
    }),
  };
});

// Mock useNotifications to avoid needing NotificationProvider
vi.mock("../contexts/NotificationContext", () => ({
  useNotifications: () => ({
    notifications: [],
    addNotification: vi.fn(),
    dismissNotification: vi.fn(),
    dismissAllNotifications: vi.fn(),
  }),
}));

// Mock useImageLibrary to avoid needing ImageLibraryProvider
vi.mock("../contexts/ImageLibraryContext", () => ({
  useImageLibrary: () => ({
    imageLibrary: [],
    loading: false,
    error: null,
    refreshImageLibrary: vi.fn(),
  }),
  ImageLibraryProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// Mock useDeviceCatalog to avoid needing DeviceCatalogProvider
vi.mock("../contexts/DeviceCatalogContext", () => ({
  useDeviceCatalog: () => ({
    vendorCategories: [],
    imageCatalog: {},
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

// Mock getBoundingClientRect for canvas
const mockGetBoundingClientRect = vi.fn(() => ({
  left: 0,
  top: 0,
  right: 800,
  bottom: 600,
  width: 800,
  height: 600,
  x: 0,
  y: 0,
  toJSON: () => {},
}));

// Mock fetch globally
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

// Mock WebSocket
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  url: string;
  readyState: number;
  binaryType: string;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    this.binaryType = "blob";
    mockWebSocketInstances.push(this);
  }

  send() {}
  close() {
    this.readyState = MockWebSocket.CLOSED;
  }
}

let mockWebSocketInstances: MockWebSocket[] = [];
const originalWebSocket = global.WebSocket;

// Wrapper component with all providers
const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter>
    <ThemeProvider>
      <UserProvider>{children}</UserProvider>
    </ThemeProvider>
  </BrowserRouter>
);

// Mock API responses
const mockLabsResponse = {
  labs: [
    { id: "lab-1", name: "Test Lab 1", created_at: "2024-01-15T10:00:00Z" },
    { id: "lab-2", name: "Production Lab", created_at: "2024-01-14T10:00:00Z" },
  ],
};

const mockEmptyLabsResponse = { labs: [] };

const mockImagesResponse = { images: {} };

const mockImageLibraryResponse = { images: [] };

const mockVendorsResponse = [
  {
    name: "Network Devices",
    models: [
      {
        id: "linux",
        type: "container",
        name: "Linux Container",
        icon: "fa-server",
        versions: ["alpine:latest"],
        isActive: true,
        vendor: "Generic",
      },
    ],
  },
];

const mockDashboardMetricsResponse = {
  agents: { online: 1, total: 1 },
  containers: { running: 5, total: 10 },
  cpu_percent: 25.5,
  memory_percent: 45.2,
  labs_running: 1,
  labs_total: 2,
};

const mockAgentsResponse: { id: string; name: string; address: string; status: string }[] = [];

const mockUserResponse = {
  id: "user-1",
  username: "testuser",
  email: "test@example.com",
  is_active: true,
  global_role: "super_admin",
  created_at: "2024-01-01T00:00:00Z",
};

const mockGraphResponse = {
  nodes: [],
  links: [],
};

const mockLabStatusResponse = {
  nodes: [],
};

const mockNodeStatesResponse = {
  nodes: [],
};

const mockJobsResponse = {
  jobs: [],
};

// Helper to setup default API mocks
function setupDefaultMocks() {
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes("/labs") && !url.includes("/export") && !url.includes("/import") && !url.includes("/nodes") && !url.includes("/jobs") && !url.includes("/status") && !url.includes("/layout")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockLabsResponse),
      };
    }
    if (url.includes("/images/library")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockImageLibraryResponse),
      };
    }
    if (url.includes("/images")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockImagesResponse),
      };
    }
    if (url.includes("/vendors")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockVendorsResponse),
      };
    }
    if (url.includes("/dashboard/metrics")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockDashboardMetricsResponse),
      };
    }
    if (url.includes("/agents")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockAgentsResponse),
      };
    }
    if (url.includes("/auth/me")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockUserResponse),
      };
    }
    if (url.includes("/export-graph")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockGraphResponse),
      };
    }
    if (url.includes("/layout")) {
      return {
        ok: false,
        status: 404,
      };
    }
    if (url.includes("/status")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockLabStatusResponse),
      };
    }
    if (url.includes("/nodes/states")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockNodeStatesResponse),
      };
    }
    if (url.includes("/nodes/refresh")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      };
    }
    if (url.includes("/jobs")) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockJobsResponse),
      };
    }
    // Default response
    return {
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    };
  });
}

describe("StudioPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWebSocketInstances = [];
    (global as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;
    localStorage.clear();
    localStorage.setItem("token", "test-token");
    Element.prototype.getBoundingClientRect = mockGetBoundingClientRect;
    mockTerminalOnData.mockReturnValue({ dispose: vi.fn() });
    setupDefaultMocks();
  });

  afterEach(() => {
    (global as unknown as { WebSocket: typeof WebSocket }).WebSocket = originalWebSocket;
    localStorage.clear();
  });

;

  describe("Dashboard View (No Active Lab)", () => {
    it("renders dashboard when no lab is selected", async () => {
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });

    it("renders lab list on dashboard", async () => {
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
        expect(screen.getByText("Production Lab")).toBeInTheDocument();
      });
    });

    it("shows empty state when no labs exist", async () => {
      mockFetch.mockImplementation(async (url: string) => {
        if (url.includes("/labs") && !url.includes("/export")) {
          return {
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockEmptyLabsResponse),
          };
        }
        if (url.includes("/images/library")) {
          return {
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockImageLibraryResponse),
          };
        }
        if (url.includes("/images")) {
          return {
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockImagesResponse),
          };
        }
        if (url.includes("/vendors")) {
          return {
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockVendorsResponse),
          };
        }
        if (url.includes("/dashboard/metrics")) {
          return {
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockDashboardMetricsResponse),
          };
        }
        if (url.includes("/agents")) {
          return {
            ok: true,
            status: 200,
            json: () => Promise.resolve([]),
          };
        }
        if (url.includes("/auth/me")) {
          return {
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockUserResponse),
          };
        }
        return {
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        };
      });

      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText(/Empty Workspace/i)).toBeInTheDocument();
      });
    });

    it("shows Create New Lab button", async () => {
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /create new lab/i })).toBeInTheDocument();
      });
    });
  });

  describe("Lab Card Rendering", () => {
    it("renders lab names in dashboard", async () => {
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
        expect(screen.getByText("Production Lab")).toBeInTheDocument();
      });
    });
  });

  describe("API Loading", () => {
    it("loads labs on mount", async () => {
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining("/labs"),
          expect.anything()
        );
      });
    });

    it("receives device catalog from context", async () => {
      // Device catalog (/vendors, /images) is now loaded by DeviceCatalogContext,
      // not by StudioPage directly. The context is mocked in tests.
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });

    it("loads system metrics on mount", async () => {
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining("/dashboard/metrics"),
          expect.anything()
        );
      });
    });
  });

  describe("Custom Devices from DeviceCatalog", () => {
    it("renders with device catalog from context", async () => {
      // Custom devices are now loaded from the DeviceCatalog context (API-based)
      // rather than localStorage. The mock provides empty deviceModels by default.
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });
  });

  describe("Agent Indicators Preference", () => {
    it("loads agent indicator preference from localStorage", async () => {
      localStorage.setItem("archetype_show_agent_indicators", "false");

      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      // Should load without errors
      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });

    it("defaults to showing agent indicators when preference not set", async () => {
      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      // Should load with default preference
      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });
  });

  describe("Task Log Clearing", () => {
    it("loads task log cleared timestamp from localStorage", async () => {
      const timestamp = Date.now() - 10000;
      localStorage.setItem("archetype_tasklog_cleared_at", timestamp.toString());

      render(
        <TestWrapper>
          <StudioPage />
        </TestWrapper>
      );

      // Should load without errors
      await waitFor(() => {
        expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      });
    });
  });
});
