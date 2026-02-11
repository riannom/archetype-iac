import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import StudioConsolePage from "./StudioConsolePage";

// Use vi.hoisted to ensure these are available during mock initialization
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

// Mock FitAddon
vi.mock("xterm-addon-fit", () => ({
  FitAddon: MockFitAddon,
}));

// Mock API_BASE_URL
vi.mock("../api", () => ({
  API_BASE_URL: "/api",
}));

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
  sentMessages: unknown[] = [];

  constructor(url: string) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    this.binaryType = "blob";
    mockWebSocketInstances.push(this);
  }

  send(data: unknown) {
    if (this.readyState === MockWebSocket.OPEN) {
      this.sentMessages.push(data);
    }
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) {
      this.onclose(new CloseEvent("close"));
    }
  }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    if (this.onopen) {
      this.onopen(new Event("open"));
    }
  }
}

let mockWebSocketInstances: MockWebSocket[] = [];
const originalWebSocket = global.WebSocket;

// Helper to render with router
function renderWithRouter(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/studio/:labId/console/:nodeId" element={<StudioConsolePage />} />
        <Route path="/" element={<div>Dashboard</div>} />
        <Route path="*" element={<div>Not Found</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe("StudioConsolePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWebSocketInstances = [];
    (global as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;
    localStorage.clear();

    // Setup onData to return a disposable
    mockTerminalOnData.mockReturnValue({
      dispose: vi.fn(),
    });
  });

  afterEach(() => {
    (global as unknown as { WebSocket: typeof WebSocket }).WebSocket = originalWebSocket;
    localStorage.clear();
  });

  describe("Authentication", () => {
    it("redirects to dashboard when no token is present", () => {
      renderWithRouter("/studio/lab-123/console/router1");

      expect(screen.getByText("Dashboard")).toBeInTheDocument();
    });

    it("renders console when token is present", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router1");

      expect(screen.queryByText("Dashboard")).not.toBeInTheDocument();
      expect(screen.getByText(/Console:/)).toBeInTheDocument();
    });
  });

  describe("Missing Parameters", () => {
    it("shows error message when labId is missing", () => {
      localStorage.setItem("token", "valid-token");

      render(
        <MemoryRouter initialEntries={["/studio//console/router1"]}>
          <Routes>
            <Route path="/studio/:labId/console/:nodeId" element={<StudioConsolePage />} />
            <Route path="/studio/console/:nodeId" element={<StudioConsolePage />} />
            <Route path="*" element={<StudioConsolePage />} />
          </Routes>
        </MemoryRouter>
      );

      // When params are missing from URL, shows error message
      expect(screen.getByText("Missing console parameters.")).toBeInTheDocument();
    });

    it("shows error message when nodeId is missing", () => {
      localStorage.setItem("token", "valid-token");

      render(
        <MemoryRouter initialEntries={["/studio/lab-123/console/"]}>
          <Routes>
            <Route path="/studio/:labId/console/:nodeId" element={<StudioConsolePage />} />
            <Route path="/studio/:labId/console/" element={<StudioConsolePage />} />
            <Route path="*" element={<StudioConsolePage />} />
          </Routes>
        </MemoryRouter>
      );

      // Missing nodeId should show error
      expect(screen.getByText("Missing console parameters.")).toBeInTheDocument();
    });
  });

  describe("Rendering", () => {
    it("renders the header with node name", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router1");

      expect(screen.getByText("Console:")).toBeInTheDocument();
      expect(screen.getByText("router1")).toBeInTheDocument();
    });

    it("renders the header with lab ID", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router1");

      expect(screen.getByText("Lab lab-123")).toBeInTheDocument();
    });

    it("has correct page styling", () => {
      localStorage.setItem("token", "valid-token");

      const { container } = renderWithRouter("/studio/lab-123/console/router1");

      const pageContainer = container.querySelector(".min-h-screen");
      expect(pageContainer).toBeInTheDocument();
      expect(pageContainer).toHaveClass("bg-stone-50/72");
    });

    it("renders the terminal container", () => {
      localStorage.setItem("token", "valid-token");

      const { container } = renderWithRouter("/studio/lab-123/console/router1");

      const terminalContainer = container.querySelector(".glass-surface");
      expect(terminalContainer).toBeInTheDocument();
    });
  });

  describe("Terminal Session Integration", () => {
    it("initializes terminal with correct parameters", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router1");

      expect(MockTerminal).toHaveBeenCalled();
      expect(mockTerminalOpen).toHaveBeenCalled();
    });

    it("creates WebSocket connection with correct lab and node IDs", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router1");

      expect(mockWebSocketInstances.length).toBe(1);
      expect(mockWebSocketInstances[0].url).toContain("/labs/lab-123/nodes/router1/console");
    });

    it("handles URL-encoded node IDs correctly", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router%2Fspecial");

      expect(screen.getByText("router/special")).toBeInTheDocument();
    });

    it("terminal is set as active", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router1");

      // The TerminalSession should receive isActive=true
      // When WebSocket opens, it should focus the terminal
      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      expect(mockTerminalFocus).toHaveBeenCalled();
    });
  });

  describe("Route Parameters", () => {
    it("extracts labId from URL", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/test-lab-abc/console/node1");

      expect(screen.getByText("Lab test-lab-abc")).toBeInTheDocument();
    });

    it("extracts nodeId from URL", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/my-router");

      expect(screen.getByText("my-router")).toBeInTheDocument();
    });

    it("handles special characters in labId", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab_123-test/console/node1");

      expect(screen.getByText("Lab lab_123-test")).toBeInTheDocument();
    });

    it("handles special characters in nodeId", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/node_with-special.chars");

      expect(screen.getByText("node_with-special.chars")).toBeInTheDocument();
    });
  });

  describe("Layout", () => {
    it("has a header section", () => {
      localStorage.setItem("token", "valid-token");

      const { container } = renderWithRouter("/studio/lab-123/console/router1");

      const header = container.querySelector("header");
      expect(header).toBeInTheDocument();
    });

    it("has a main content area for the terminal", () => {
      localStorage.setItem("token", "valid-token");

      const { container } = renderWithRouter("/studio/lab-123/console/router1");

      const mainArea = container.querySelector(".flex-1");
      expect(mainArea).toBeInTheDocument();
    });

    it("terminal container has border styling", () => {
      localStorage.setItem("token", "valid-token");

      const { container } = renderWithRouter("/studio/lab-123/console/router1");

      const terminalWrapper = container.querySelector(".rounded-xl.overflow-hidden");
      expect(terminalWrapper).toBeInTheDocument();
    });
  });

  describe("Edge Cases", () => {
    it("cleans up terminal on unmount", () => {
      localStorage.setItem("token", "valid-token");

      const { unmount } = renderWithRouter("/studio/lab-123/console/router1");

      unmount();

      expect(mockTerminalDispose).toHaveBeenCalled();
    });

    it("closes WebSocket on unmount", () => {
      localStorage.setItem("token", "valid-token");

      const { unmount } = renderWithRouter("/studio/lab-123/console/router1");

      const ws = mockWebSocketInstances[0];
      const closeSpy = vi.spyOn(ws, "close");

      unmount();

      expect(closeSpy).toHaveBeenCalled();
    });

    it("handles empty string token as no authentication", () => {
      localStorage.setItem("token", "");

      renderWithRouter("/studio/lab-123/console/router1");

      // Empty token should be treated as no auth - redirect to dashboard
      expect(screen.getByText("Dashboard")).toBeInTheDocument();
    });

    it("handles whitespace-only token as valid authentication", () => {
      localStorage.setItem("token", "   ");

      renderWithRouter("/studio/lab-123/console/router1");

      // Whitespace token is truthy, so should render console
      expect(screen.queryByText("Dashboard")).not.toBeInTheDocument();
    });
  });

  describe("Accessibility", () => {
    it("has proper heading structure", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router1");

      // Check that node name is displayed prominently
      const nodeText = screen.getByText("router1");
      expect(nodeText).toHaveClass("text-sage-400");
    });

    it("displays lab context information", () => {
      localStorage.setItem("token", "valid-token");

      renderWithRouter("/studio/lab-123/console/router1");

      const labInfo = screen.getByText("Lab lab-123");
      expect(labInfo).toHaveClass("uppercase", "tracking-widest");
    });
  });
});
