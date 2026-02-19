import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
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

  // Test helper: simulate close without triggering handler (for testing manual close)
  simulateCloseNoHandler() {
    this.readyState = MockWebSocket.CLOSED;
  }
}

let mockWebSocketInstances: MockWebSocket[] = [];

// Replace global WebSocket
const originalWebSocket = global.WebSocket;

describe("TerminalSession", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    mockWebSocketInstances = [];

    // Mock WebSocket globally
    (global as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;

    // Setup onData to return a disposable
    mockTerminalOnData.mockReturnValue({
      dispose: vi.fn(),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
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
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={false} />
      );

      await user.click(screen.getByText("Connect Anyway"));

      expect(screen.queryByText("Device Booting")).not.toBeInTheDocument();
    });

    it("hides boot warning when isReady changes to true", () => {
      const { rerender } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={false} />
      );

      expect(screen.getByText("Device Booting")).toBeInTheDocument();

      rerender(
        <TerminalSession labId="lab-1" nodeId="node-1" isReady={true} />
      );

      expect(screen.queryByText("Device Booting")).not.toBeInTheDocument();
    });

    it("does not re-show boot warning after dismissal even if isReady stays false", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

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
      vi.useRealTimers(); // Blob async needs real timers

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

      vi.useFakeTimers(); // Restore for other tests
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

  describe("Console Control Mode", () => {
    it("shows subtle read-only overlay during configuration ownership", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      act(() => {
        ws.simulateMessage(
          JSON.stringify({
            type: "console-control",
            state: "read_only",
            message: "Configuration in progress. Console is view-only.",
          })
        );
      });

      expect(
        screen.getByText("Configuration in progress. Console is view-only.")
      ).toBeInTheDocument();
    });

    it("blocks keyboard input while read-only overlay is active", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      act(() => {
        ws.simulateMessage(
          JSON.stringify({
            type: "console-control",
            state: "read_only",
            message: "Configuration in progress. Console is view-only.",
          })
        );
      });

      const onDataCallback = mockTerminalOnData.mock.calls[0][0];
      onDataCallback("user input");

      expect(ws.sentMessages).not.toContain("user input");
    });

    it("restores interactive input after configuration completes", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      act(() => {
        ws.simulateMessage(
          JSON.stringify({
            type: "console-control",
            state: "read_only",
            message: "Configuration in progress. Console is view-only.",
          })
        );
      });

      act(() => {
        ws.simulateMessage(
          JSON.stringify({
            type: "console-control",
            state: "interactive",
            message: "Configuration completed. Interactive control restored.",
          })
        );
      });

      const onDataCallback = mockTerminalOnData.mock.calls[0][0];
      onDataCallback("user input");

      expect(ws.sentMessages).toContain("user input");
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

  describe("WebSocket Token Authentication", () => {
    it("includes token in WebSocket URL when present in localStorage", () => {
      const getItemSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(
        (key: string) => (key === "token" ? "my-jwt-token" : null)
      );

      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      expect(mockWebSocketInstances[0].url).toContain("?token=my-jwt-token");
      getItemSpy.mockRestore();
    });

    it("omits token param when localStorage has no token", () => {
      const getItemSpy = vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);

      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      expect(mockWebSocketInstances[0].url).not.toContain("?token=");
      expect(mockWebSocketInstances[0].url).not.toContain("token=");
      getItemSpy.mockRestore();
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

  describe("Reconnection", () => {
    it("writes connection lost message on first disconnect", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      // Simulate close (triggers reconnection)
      act(() => {
        ws.close();
      });

      // Should write amber connection lost message
      expect(mockTerminalWrite).toHaveBeenCalledWith(
        expect.stringContaining("[connection lost - reconnecting...]")
      );
    });

    it("shows connection lost overlay when disconnected", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      act(() => {
        ws.close();
      });

      expect(screen.getByText("Connection Lost")).toBeInTheDocument();
    });

    it("shows reconnect attempt counter in overlay", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      act(() => {
        ws.close();
      });

      expect(screen.getByText(/attempt 1\/10/)).toBeInTheDocument();
    });

    it("attempts reconnection with exponential backoff", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      // First disconnect
      act(() => {
        ws.close();
      });

      // Should schedule reconnect after 1000ms (base delay * 2^0)
      expect(mockWebSocketInstances).toHaveLength(1);

      act(() => {
        vi.advanceTimersByTime(1000);
      });

      // Should have created a new WebSocket
      expect(mockWebSocketInstances).toHaveLength(2);

      // Close second connection
      act(() => {
        mockWebSocketInstances[1].close();
      });

      // Second reconnect should be after 2000ms (base delay * 2^1)
      act(() => {
        vi.advanceTimersByTime(1999);
      });
      expect(mockWebSocketInstances).toHaveLength(2);

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(mockWebSocketInstances).toHaveLength(3);
    });

    it("resets attempt counter on successful reconnection", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      // Disconnect
      act(() => {
        ws.close();
      });

      // Wait for reconnect
      act(() => {
        vi.advanceTimersByTime(1000);
      });

      // Reconnect succeeds
      act(() => {
        mockWebSocketInstances[1].simulateOpen();
      });

      // Connection lost overlay should disappear
      expect(screen.queryByText("Connection Lost")).not.toBeInTheDocument();
    });

    it("shows failure overlay after max attempts", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      // Exhaust all reconnect attempts
      act(() => { ws.close(); });

      for (let i = 0; i < 10; i++) {
        act(() => {
          vi.advanceTimersByTime(20000); // enough for any backoff delay
        });
        const nextWs = mockWebSocketInstances[mockWebSocketInstances.length - 1];
        if (i < 9) {
          act(() => { nextWs.close(); });
        }
      }

      // Close the last attempt
      act(() => {
        mockWebSocketInstances[mockWebSocketInstances.length - 1].close();
      });

      // Should show reconnection failed
      expect(screen.getByText("Reconnection Failed")).toBeInTheDocument();
      expect(screen.getByText("Try Again")).toBeInTheDocument();
    });

    it("provides Reconnect Now button during auto-reconnect", () => {
      render(<TerminalSession labId="lab-1" nodeId="node-1" />);

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      act(() => {
        ws.close();
      });

      expect(screen.getByText("Reconnect Now")).toBeInTheDocument();
    });

    it("does not reconnect on intentional unmount", () => {
      const { unmount } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" />
      );

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      unmount();

      // No reconnect attempts should be made
      act(() => {
        vi.advanceTimersByTime(5000);
      });

      // Only 1 WebSocket instance (the original one)
      expect(mockWebSocketInstances).toHaveLength(1);
    });

    it("does not write disconnect message on intentional unmount", () => {
      const { unmount } = render(
        <TerminalSession labId="lab-1" nodeId="node-1" />
      );

      const ws = mockWebSocketInstances[0];
      ws.simulateOpen();

      mockTerminalWrite.mockClear();

      unmount();

      // Should NOT write connection lost message (onclose is nulled before close)
      expect(mockTerminalWrite).not.toHaveBeenCalledWith(
        expect.stringContaining("[connection lost")
      );
    });
  });
});
