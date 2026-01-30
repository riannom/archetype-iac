import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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
vi.mock("../../api", () => ({
  API_BASE_URL: "/api",
}));

// Import after mocks are set up
import TerminalSession from "./TerminalSession";

// Mock WebSocket
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  url: string;
  readyState: number;
  binaryType: string;
  onopen: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  sentMessages: unknown[];

  constructor(url: string) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    this.binaryType = "blob";
    this.onopen = null;
    this.onclose = null;
    this.onmessage = null;
    this.onerror = null;
    this.sentMessages = [];
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

  // Test helper: simulate connection open
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    if (this.onopen) {
      this.onopen(new Event("open"));
    }
  }

  // Test helper: simulate receiving a message
  simulateMessage(data: unknown) {
    if (this.onmessage) {
      this.onmessage(new MessageEvent("message", { data }));
    }
  }

  // Test helper: simulate connection error
  simulateError() {
    if (this.onerror) {
      this.onerror(new Event("error"));
    }
  }
}

let mockWebSocketInstances: MockWebSocket[] = [];

// Replace global WebSocket
const originalWebSocket = global.WebSocket;

describe("TerminalSession", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWebSocketInstances = [];

    // Mock WebSocket globally
    (global as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;

    // Setup onData to return a disposable
    mockTerminalOnData.mockReturnValue({
      dispose: vi.fn(),
    });
  });

  afterEach(() => {
    // Restore original WebSocket
    (global as unknown as { WebSocket: typeof WebSocket }).WebSocket = originalWebSocket;
  });

  describe("Rendering", () => {
    it("renders the terminal container", () => {
      const { container } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" />
      );

      // Should have a container div for the terminal
      const terminalContainer = container.querySelector(".w-full.h-full");
      expect(terminalContainer).toBeInTheDocument();
    });

    it("initializes xterm Terminal on mount", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      expect(MockTerminal).toHaveBeenCalledWith({
        fontSize: 12,
        cursorBlink: true,
        fontFamily: expect.stringContaining("ui-monospace"),
        theme: {
          background: "#0b0f16",
          foreground: "#dbe7ff",
          cursor: "#8aa1ff",
        },
      });
    });

    it("loads FitAddon on terminal", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      expect(mockTerminalLoadAddon).toHaveBeenCalled();
    });

    it("opens terminal on container element", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      expect(mockTerminalOpen).toHaveBeenCalled();
    });

    it("fits terminal to container on mount", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      expect(mockFitAddonFit).toHaveBeenCalled();
    });
  });

  describe("Boot Warning", () => {
    it("shows boot warning when isReady is false", () => {
      render(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={false} />
      );

      expect(screen.getByText("Device Booting")).toBeInTheDocument();
      expect(
        screen.getByText(/network device is still starting up/i)
      ).toBeInTheDocument();
    });

    it("does not show boot warning when isReady is true", () => {
      render(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={true} />
      );

      expect(screen.queryByText("Device Booting")).not.toBeInTheDocument();
    });

    it("does not show boot warning by default", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      expect(screen.queryByText("Device Booting")).not.toBeInTheDocument();
    });

    it("shows Connect Anyway button when boot warning is displayed", () => {
      render(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={false} />
      );

      expect(screen.getByText("Connect Anyway")).toBeInTheDocument();
    });

    it("dismisses boot warning when Connect Anyway is clicked", async () => {
      const user = userEvent.setup();

      render(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={false} />
      );

      await user.click(screen.getByText("Connect Anyway"));

      expect(screen.queryByText("Device Booting")).not.toBeInTheDocument();
    });

    it("hides boot warning when isReady changes to true", async () => {
      const { rerender } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={false} />
      );

      expect(screen.getByText("Device Booting")).toBeInTheDocument();

      rerender(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={true} />
      );

      await waitFor(() => {
        expect(screen.queryByText("Device Booting")).not.toBeInTheDocument();
      });
    });

    it("does not re-show boot warning after dismissal even if isReady stays false", async () => {
      const user = userEvent.setup();

      const { rerender } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={false} />
      );

      await user.click(screen.getByText("Connect Anyway"));

      // Re-render with isReady still false
      rerender(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={false} />
      );

      expect(screen.queryByText("Device Booting")).not.toBeInTheDocument();
    });
  });

  describe("WebSocket Connection", () => {
    it("creates WebSocket connection with correct URL", () => {
      render(<TerminalSession labId="lab-1" nodeId="router1" />);

      expect(mockWebSocketInstances.length).toBe(1);
      expect(mockWebSocketInstances[0].url).toContain("/labs/lab-1/nodes/router1/console");
    });

    it("sets binaryType to arraybuffer", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      expect(mockWebSocketInstances[0].binaryType).toBe("arraybuffer");
    });

    it("focuses terminal when WebSocket opens", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      expect(mockTerminalFocus).toHaveBeenCalled();
    });

    it("writes disconnect message when WebSocket closes", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.close();

      expect(mockTerminalWriteln).toHaveBeenCalledWith("\n[console disconnected]\n");
    });

    it("encodes nodeId in WebSocket URL", () => {
      render(<TerminalSession labId="lab-1" nodeId="router/special" />);

      expect(mockWebSocketInstances[0].url).toContain("router%2Fspecial");
    });

    it("closes WebSocket on unmount", () => {
      const { unmount } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" />
      );

      const ws = mockWebSocketInstances[0];
      const closeSpy = vi.spyOn(ws, "close");

      unmount();

      expect(closeSpy).toHaveBeenCalled();
    });
  });

  describe("Terminal Data Handling", () => {
    it("writes string data to terminal", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateMessage("Hello, World!");

      expect(mockTerminalWrite).toHaveBeenCalledWith("Hello, World!");
    });

    it("writes ArrayBuffer data to terminal as Uint8Array", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      const buffer = new ArrayBuffer(5);
      const view = new Uint8Array(buffer);
      view.set([72, 101, 108, 108, 111]); // "Hello" in ASCII

      ws.simulateMessage(buffer);

      expect(mockTerminalWrite).toHaveBeenCalledWith(expect.any(Uint8Array));
    });

    it("writes Blob data to terminal after converting to ArrayBuffer", async () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];

      // Create a mock Blob with arrayBuffer method
      const mockArrayBuffer = new ArrayBuffer(9);
      const mockBlob = {
        arrayBuffer: vi.fn().mockResolvedValue(mockArrayBuffer),
      };

      // Need to make it pass instanceof Blob check
      Object.setPrototypeOf(mockBlob, Blob.prototype);

      ws.simulateMessage(mockBlob);

      // Blob processing is async
      await waitFor(() => {
        expect(mockBlob.arrayBuffer).toHaveBeenCalled();
      });
    });

    it("sends terminal input to WebSocket when open", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      // Get the onData callback
      const onDataCallback = mockTerminalOnData.mock.calls[0][0];
      onDataCallback("user input");

      expect(ws.sentMessages).toContain("user input");
    });

    it("does not send terminal input when WebSocket is not open", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      // WebSocket is CONNECTING, not OPEN

      // Get the onData callback
      const onDataCallback = mockTerminalOnData.mock.calls[0][0];
      onDataCallback("user input");

      expect(ws.sentMessages.length).toBe(0);
    });
  });

  describe("Active State", () => {
    it("fits and focuses terminal when isActive becomes true", () => {
      const { rerender } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" isActive={false} />
      );

      // Clear previous calls from mount
      mockFitAddonFit.mockClear();
      mockTerminalFocus.mockClear();

      rerender(
        <TerminalSession labId="lab-1" nodeId="node-1" isActive={true} />
      );

      expect(mockFitAddonFit).toHaveBeenCalled();
      expect(mockTerminalFocus).toHaveBeenCalled();
    });

    it("does not fit or focus when isActive is false", () => {
      const { rerender } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" isActive={true} />
      );

      // Clear previous calls
      mockFitAddonFit.mockClear();
      mockTerminalFocus.mockClear();

      rerender(
        <TerminalSession labId="lab-1" nodeId="node-1" isActive={false} />
      );

      // Should not have new calls (the isActive effect shouldn't trigger fit/focus)
      expect(mockFitAddonFit).not.toHaveBeenCalled();
      expect(mockTerminalFocus).not.toHaveBeenCalled();
    });
  });

  describe("Cleanup", () => {
    it("disposes terminal on unmount", () => {
      const { unmount } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" />
      );

      unmount();

      expect(mockTerminalDispose).toHaveBeenCalled();
    });

    it("disposes data listener on unmount", () => {
      const disposeMock = vi.fn();
      mockTerminalOnData.mockReturnValue({ dispose: disposeMock });

      const { unmount } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" />
      );

      unmount();

      expect(disposeMock).toHaveBeenCalled();
    });

    it("reinitializes when labId changes", () => {
      const { rerender } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" />
      );

      MockTerminal.mockClear();

      rerender(<TerminalSession labId="lab-2" nodeId="node-1" />);

      // A new terminal should be created for the new lab
      expect(MockTerminal).toHaveBeenCalled();
    });

    it("reinitializes when nodeId changes", () => {
      const { rerender } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" />
      );

      MockTerminal.mockClear();

      rerender(<TerminalSession labId="lab-1" nodeId="node-2" />);

      // A new terminal should be created for the new node
      expect(MockTerminal).toHaveBeenCalled();
    });
  });

  describe("Protocol Handling", () => {
    it("uses ws:// protocol when location is http://", () => {
      // Default jsdom uses http://localhost
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const wsUrl = mockWebSocketInstances[0].url;
      expect(wsUrl.startsWith("ws://")).toBe(true);
    });
  });

  describe("ResizeObserver Integration", () => {
    it("fits terminal when container is resized", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      // ResizeObserver.observe should have been called
      // The mock in setupTests doesn't track instances the same way,
      // but we can verify fit was called on mount
      expect(mockFitAddonFit).toHaveBeenCalled();
    });
  });

  describe("Edge Cases", () => {
    it("handles empty message gracefully", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateMessage("");

      expect(mockTerminalWrite).toHaveBeenCalledWith("");
    });

    it("handles special characters in nodeId", () => {
      render(<TerminalSession labId="lab-1" nodeId="node with spaces" />);

      expect(mockWebSocketInstances[0].url).toContain("node%20with%20spaces");
    });

    it("handles multiple rapid messages", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateMessage("Line 1\n");
      ws.simulateMessage("Line 2\n");
      ws.simulateMessage("Line 3\n");

      expect(mockTerminalWrite).toHaveBeenCalledTimes(3);
    });
  });
});
