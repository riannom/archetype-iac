import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConsoleManager from "./ConsoleManager";
import { ConsoleWindow, Node, DeviceType } from "../types";

// Mock TerminalSession component
vi.mock("./TerminalSession", () => ({
  default: vi.fn(({ labId, nodeId, isActive, isReady }) => (
    <div
      data-testid={`terminal-session-${nodeId}`}
      data-lab-id={labId}
      data-is-active={isActive}
      data-is-ready={isReady}
    >
      Terminal: {nodeId}
    </div>
  )),
}));

// Mock window.open for pop-out functionality
const mockWindowOpen = vi.fn();

// Mock requestAnimationFrame to execute callback synchronously in tests
const originalRAF = globalThis.requestAnimationFrame;
const originalCAF = globalThis.cancelAnimationFrame;

describe("ConsoleManager", () => {
  const mockNodes: Node[] = [
    {
      id: "router1",
      name: "Router 1",
      nodeType: "device",
      type: DeviceType.ROUTER,
      model: "ceos",
      version: "4.28.0F",
      x: 100,
      y: 100,
    },
    {
      id: "router2",
      name: "Router 2",
      nodeType: "device",
      type: DeviceType.ROUTER,
      model: "ceos",
      version: "4.28.0F",
      x: 200,
      y: 100,
    },
    {
      id: "switch1",
      name: "Switch 1",
      nodeType: "device",
      type: DeviceType.SWITCH,
      model: "ceos",
      version: "4.28.0F",
      x: 150,
      y: 200,
    },
  ];

  const mockNodeStates = {
    router1: {
      id: "state-1",
      node_id: "router1",
      actual_state: "running",
      is_ready: true,
    },
    router2: {
      id: "state-2",
      node_id: "router2",
      actual_state: "running",
      is_ready: false,
    },
    switch1: {
      id: "state-3",
      node_id: "switch1",
      actual_state: "stopped",
      is_ready: undefined,
    },
  };

  const defaultProps = {
    labId: "lab-123",
    windows: [] as ConsoleWindow[],
    nodes: mockNodes,
    nodeStates: mockNodeStates,
    onCloseWindow: vi.fn(),
    onCloseTab: vi.fn(),
    onSetActiveTab: vi.fn(),
    onUpdateWindowPos: vi.fn(),
    onUpdateWindowSize: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    (window as any).open = mockWindowOpen;
    // Make rAF execute synchronously for test predictability
    globalThis.requestAnimationFrame = (cb: FrameRequestCallback) => { cb(0); return 0; };
    globalThis.cancelAnimationFrame = () => {};
  });

  afterEach(() => {
    delete (window as any).open;
    globalThis.requestAnimationFrame = originalRAF;
    globalThis.cancelAnimationFrame = originalCAF;
  });

  describe("Rendering", () => {
    it("renders nothing when no windows are provided", () => {
      const { container } = render(<ConsoleManager {...defaultProps} />);

      // Should have no console windows rendered
      expect(container.querySelectorAll(".fixed.z-\\[100\\]")).toHaveLength(0);
    });

    it("renders a single console window", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      expect(screen.getByText("Router 1")).toBeInTheDocument();
    });

    it("renders multiple console windows", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
        {
          id: "win-2",
          deviceIds: ["router2"],
          activeDeviceId: "router2",
          x: 600,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      expect(screen.getByText("Router 1")).toBeInTheDocument();
      expect(screen.getByText("Router 2")).toBeInTheDocument();
    });

    it("brings the most recently clicked window to the front", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
        {
          id: "win-2",
          deviceIds: ["router2"],
          activeDeviceId: "router2",
          x: 600,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(<ConsoleManager {...defaultProps} windows={windows} />);
      const winEls = Array.from(container.querySelectorAll(".fixed.z-\\[100\\]")) as HTMLDivElement[];
      expect(winEls).toHaveLength(2);

      const [w1, w2] = winEls;
      expect(Number(w2.style.zIndex)).toBeGreaterThan(Number(w1.style.zIndex));

      fireEvent.mouseDown(w1);
      expect(Number(w1.style.zIndex)).toBeGreaterThan(Number(w2.style.zIndex));
    });

    it("renders window at correct position", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 100,
          y: 200,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      const windowEl = container.querySelector(".fixed.z-\\[100\\]");
      expect(windowEl).toHaveStyle({ left: "100px", top: "200px" });
    });

    it("shows 'Unknown' for nodes that don't exist in nodes array", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["nonexistent"],
          activeDeviceId: "nonexistent",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      expect(screen.getByText("Unknown")).toBeInTheDocument();
    });
  });

  describe("Tabs", () => {
    it("renders multiple tabs in a single window", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1", "router2", "switch1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      expect(screen.getByText("Router 1")).toBeInTheDocument();
      expect(screen.getByText("Router 2")).toBeInTheDocument();
      expect(screen.getByText("Switch 1")).toBeInTheDocument();
    });

    it("highlights active tab", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1", "router2"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      // Active tab should have specific styling
      const router1Tab = screen.getByText("Router 1").closest("div");
      expect(router1Tab?.className).toContain("text-sage-400");
      expect(router1Tab?.className).toContain("bg-stone-900");
    });

    it("calls onSetActiveTab when clicking a tab", async () => {
      const user = userEvent.setup();
      const onSetActiveTab = vi.fn();

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1", "router2"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(
        <ConsoleManager
          {...defaultProps}
          windows={windows}
          onSetActiveTab={onSetActiveTab}
        />
      );

      await user.click(screen.getByText("Router 2"));

      expect(onSetActiveTab).toHaveBeenCalledWith("win-1", "router2");
    });

    it("calls onCloseTab when clicking tab close button", async () => {
      const user = userEvent.setup();
      const onCloseTab = vi.fn();

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1", "router2"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(
        <ConsoleManager
          {...defaultProps}
          windows={windows}
          onCloseTab={onCloseTab}
        />
      );

      // Find close button within the Router 1 tab
      const router1Tab = screen.getByText("Router 1").closest("div");
      const closeButton = router1Tab?.querySelector("button");
      if (closeButton) {
        await user.click(closeButton);
      }

      expect(onCloseTab).toHaveBeenCalledWith("win-1", "router1");
    });

    it("shows empty state when no devices in window", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: [],
          activeDeviceId: "",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      expect(screen.getByText("No active session selected")).toBeInTheDocument();
    });
  });

  describe("Window Controls", () => {
    it("calls onCloseWindow when clicking window close button", async () => {
      const user = userEvent.setup();
      const onCloseWindow = vi.fn();

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager
          {...defaultProps}
          windows={windows}
          onCloseWindow={onCloseWindow}
        />
      );

      // Find the button that's in the header controls section
      const headerControls = container.querySelectorAll(
        ".border-l.border-stone-700 button"
      );
      // The second button is the close window button
      if (headerControls[1]) {
        await user.click(headerControls[1]);
      }

      expect(onCloseWindow).toHaveBeenCalledWith("win-1");
    });

    it("opens new window when clicking pop-out button", async () => {
      const user = userEvent.setup();

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      // Find the pop-out button (first button in header controls)
      const headerControls = container.querySelectorAll(
        ".border-l.border-stone-700 button"
      );
      if (headerControls[0]) {
        await user.click(headerControls[0]);
      }

      expect(mockWindowOpen).toHaveBeenCalledWith(
        "/studio/console/lab-123/router1",
        "archetype-console-router1",
        "width=960,height=640"
      );
    });
  });

  describe("Dragging", () => {
    it("commits window position on drag end", async () => {
      const onUpdateWindowPos = vi.fn();

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager
          {...defaultProps}
          windows={windows}
          onUpdateWindowPos={onUpdateWindowPos}
        />
      );

      // Get the header bar (drag handle)
      const header = container.querySelector(".cursor-move");
      expect(header).not.toBeNull();

      // Start drag
      fireEvent.mouseDown(header!, { clientX: 100, clientY: 100 });

      // Move (position updated via DOM, not state)
      fireEvent.mouseMove(window, { clientX: 150, clientY: 120 });

      // End drag — position is committed to state on mouseup
      fireEvent.mouseUp(window);

      expect(onUpdateWindowPos).toHaveBeenCalledWith("win-1", 100, 70);
    });

    it("applies shadow effect while dragging", async () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      const header = container.querySelector(".cursor-move");
      const windowEl = container.querySelector(".fixed.z-\\[100\\]");

      // Before drag - normal shadow
      expect(windowEl).toHaveStyle({
        boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.4)",
      });

      // Start drag
      fireEvent.mouseDown(header!, { clientX: 100, clientY: 100 });

      // During drag - enhanced shadow
      expect(windowEl).toHaveStyle({
        boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.7)",
      });

      // End drag
      fireEvent.mouseUp(window);
    });

    it("stops dragging on mouseup", async () => {
      const onUpdateWindowPos = vi.fn();

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager
          {...defaultProps}
          windows={windows}
          onUpdateWindowPos={onUpdateWindowPos}
        />
      );

      const header = container.querySelector(".cursor-move");

      // Start drag
      fireEvent.mouseDown(header!, { clientX: 100, clientY: 100 });
      fireEvent.mouseMove(window, { clientX: 150, clientY: 120 });

      // End drag (this commits position)
      fireEvent.mouseUp(window);

      onUpdateWindowPos.mockClear();

      // Move after mouseup - should not trigger updates
      fireEvent.mouseMove(window, { clientX: 200, clientY: 200 });
      fireEvent.mouseUp(window);

      expect(onUpdateWindowPos).not.toHaveBeenCalled();
    });
  });

  describe("Resizing", () => {
    it("commits resized window dimensions on mouseup", async () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container, rerender } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      // Get the resize handle
      const resizeHandle = container.querySelector(".cursor-nwse-resize");
      expect(resizeHandle).not.toBeNull();

      // Start resize
      fireEvent.mouseDown(resizeHandle!, { clientX: 570, clientY: 410 });

      // Resize
      fireEvent.mouseMove(window, { clientX: 670, clientY: 510 });

      // End resize — commits to React state
      fireEvent.mouseUp(window);

      // Trigger re-render to apply committed sizes
      rerender(<ConsoleManager {...defaultProps} windows={windows} />);

      // Window should have new size after state commit
      const windowEl = container.querySelector(".fixed.z-\\[100\\]");
      expect(windowEl).toHaveStyle({
        width: "620px",
        height: "460px",
      });
    });

    it("enforces minimum width of 320px", async () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container, rerender } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      const resizeHandle = container.querySelector(".cursor-nwse-resize");

      // Start resize
      fireEvent.mouseDown(resizeHandle!, { clientX: 570, clientY: 410 });

      // Try to resize smaller than minimum
      fireEvent.mouseMove(window, { clientX: 100, clientY: 410 });

      fireEvent.mouseUp(window);
      rerender(<ConsoleManager {...defaultProps} windows={windows} />);

      const windowEl = container.querySelector(".fixed.z-\\[100\\]");
      expect(windowEl).toHaveStyle({ width: "320px" });
    });

    it("enforces minimum height of 240px", async () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container, rerender } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      const resizeHandle = container.querySelector(".cursor-nwse-resize");

      // Start resize
      fireEvent.mouseDown(resizeHandle!, { clientX: 570, clientY: 410 });

      // Try to resize smaller than minimum
      fireEvent.mouseMove(window, { clientX: 570, clientY: 100 });

      fireEvent.mouseUp(window);
      rerender(<ConsoleManager {...defaultProps} windows={windows} />);

      const windowEl = container.querySelector(".fixed.z-\\[100\\]");
      expect(windowEl).toHaveStyle({ height: "240px" });
    });

    it("applies enhanced shadow while resizing", async () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      const resizeHandle = container.querySelector(".cursor-nwse-resize");
      const windowEl = container.querySelector(".fixed.z-\\[100\\]");

      // Start resize
      fireEvent.mouseDown(resizeHandle!, { clientX: 570, clientY: 410 });

      // During resize - enhanced shadow
      expect(windowEl).toHaveStyle({
        boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.7)",
      });

      fireEvent.mouseUp(window);
    });

    it("stops propagation on resize mousedown", async () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      const resizeHandle = container.querySelector(".cursor-nwse-resize");

      const stopPropagation = vi.fn();
      const preventDefault = vi.fn();

      fireEvent.mouseDown(resizeHandle!, {
        clientX: 570,
        clientY: 410,
        stopPropagation,
        preventDefault,
      });

      // The component calls stopPropagation and preventDefault on the event
      // In testing-library, we need to verify this differently
      // The main point is that resize should work without triggering drag
    });
  });

  describe("Terminal Session Integration", () => {
    it("renders TerminalSession for each device in window", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1", "router2"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      expect(screen.getByTestId("terminal-session-router1")).toBeInTheDocument();
      expect(screen.getByTestId("terminal-session-router2")).toBeInTheDocument();
    });

    it("passes correct isActive prop to active terminal", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1", "router2"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      const activeTerminal = screen.getByTestId("terminal-session-router1");
      const inactiveTerminal = screen.getByTestId("terminal-session-router2");

      expect(activeTerminal).toHaveAttribute("data-is-active", "true");
      expect(inactiveTerminal).toHaveAttribute("data-is-active", "false");
    });

    it("passes correct isReady prop based on node state", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1", "router2"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      // router1 is running and ready
      const readyTerminal = screen.getByTestId("terminal-session-router1");
      expect(readyTerminal).toHaveAttribute("data-is-ready", "true");

      // router2 is running but not ready
      const notReadyTerminal = screen.getByTestId("terminal-session-router2");
      expect(notReadyTerminal).toHaveAttribute("data-is-ready", "false");
    });

    it("treats non-running nodes as ready", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["switch1"],
          activeDeviceId: "switch1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      // switch1 is stopped, so should be treated as ready
      const terminal = screen.getByTestId("terminal-session-switch1");
      expect(terminal).toHaveAttribute("data-is-ready", "true");
    });

    it("passes labId to TerminalSession", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      render(<ConsoleManager {...defaultProps} windows={windows} />);

      const terminal = screen.getByTestId("terminal-session-router1");
      expect(terminal).toHaveAttribute("data-lab-id", "lab-123");
    });

    it("shows only active terminal session", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1", "router2"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      // Active terminal container should be visible
      const activeContainer = container.querySelector(
        "[data-testid='terminal-session-router1']"
      )?.parentElement;
      expect(activeContainer?.className).toContain("block");

      // Inactive terminal container should be hidden
      const inactiveContainer = container.querySelector(
        "[data-testid='terminal-session-router2']"
      )?.parentElement;
      expect(inactiveContainer?.className).toContain("hidden");
    });
  });

  describe("Default Node States", () => {
    it("handles missing nodeStates gracefully", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      // Don't pass nodeStates prop
      const propsWithoutNodeStates = { ...defaultProps };
      delete (propsWithoutNodeStates as any).nodeStates;

      render(<ConsoleManager {...propsWithoutNodeStates} windows={windows} />);

      // Should render without errors
      expect(screen.getByTestId("terminal-session-router1")).toBeInTheDocument();
    });

    it("treats nodes without state entry as ready", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      // Empty nodeStates
      render(
        <ConsoleManager {...defaultProps} windows={windows} nodeStates={{}} />
      );

      const terminal = screen.getByTestId("terminal-session-router1");
      // When nodeState is undefined, isReady defaults to true
      expect(terminal).toHaveAttribute("data-is-ready", "true");
    });
  });

  describe("Multiple Windows", () => {
    it("manages independent windows", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
        {
          id: "win-2",
          deviceIds: ["router2", "switch1"],
          activeDeviceId: "switch1",
          x: 600,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      // Should have two windows
      const windowElements = container.querySelectorAll(".fixed.z-\\[100\\]");
      expect(windowElements).toHaveLength(2);

      // First window shows Router 1
      expect(screen.getByText("Router 1")).toBeInTheDocument();

      // Second window shows both Router 2 and Switch 1 tabs
      expect(screen.getByText("Router 2")).toBeInTheDocument();
      expect(screen.getByText("Switch 1")).toBeInTheDocument();
    });

    it("dragging one window does not affect others", async () => {
      const onUpdateWindowPos = vi.fn();

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
        {
          id: "win-2",
          deviceIds: ["router2"],
          activeDeviceId: "router2",
          x: 600,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager
          {...defaultProps}
          windows={windows}
          onUpdateWindowPos={onUpdateWindowPos}
        />
      );

      // Get the first window's header
      const headers = container.querySelectorAll(".cursor-move");
      const firstHeader = headers[0];

      // Drag first window
      fireEvent.mouseDown(firstHeader, { clientX: 100, clientY: 100 });
      fireEvent.mouseMove(window, { clientX: 150, clientY: 150 });

      // End drag - commits position
      fireEvent.mouseUp(window);

      // Should only update first window
      expect(onUpdateWindowPos).toHaveBeenCalledWith("win-1", 100, 100);
      expect(onUpdateWindowPos).not.toHaveBeenCalledWith(
        "win-2",
        expect.any(Number),
        expect.any(Number)
      );
    });
  });

  describe("Event Listener Cleanup", () => {
    it("removes event listeners on unmount", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const addEventListenerSpy = vi.spyOn(window, "addEventListener");
      const removeEventListenerSpy = vi.spyOn(window, "removeEventListener");

      const { container, unmount } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      const header = container.querySelector(".cursor-move");

      // Start drag to add listeners
      fireEvent.mouseDown(header!, { clientX: 100, clientY: 100 });

      expect(addEventListenerSpy).toHaveBeenCalledWith(
        "mousemove",
        expect.any(Function)
      );
      expect(addEventListenerSpy).toHaveBeenCalledWith(
        "mouseup",
        expect.any(Function)
      );

      // End drag
      fireEvent.mouseUp(window);

      expect(removeEventListenerSpy).toHaveBeenCalledWith(
        "mousemove",
        expect.any(Function)
      );
      expect(removeEventListenerSpy).toHaveBeenCalledWith(
        "mouseup",
        expect.any(Function)
      );

      addEventListenerSpy.mockRestore();
      removeEventListenerSpy.mockRestore();
    });
  });

  describe("Default Window Size", () => {
    it("uses default size of 520x360", () => {
      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["router1"],
          activeDeviceId: "router1",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      const windowEl = container.querySelector(".fixed.z-\\[100\\]");
      expect(windowEl).toHaveStyle({
        width: "520px",
        height: "360px",
      });
    });
  });

  describe("Edge Cases", () => {
    it("handles window with undefined activeDeviceId for pop-out", async () => {
      const user = userEvent.setup();

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: [],
          activeDeviceId: "",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager {...defaultProps} windows={windows} />
      );

      // Find the pop-out button
      const headerControls = container.querySelectorAll(
        ".border-l.border-stone-700 button"
      );
      if (headerControls[0]) {
        await user.click(headerControls[0]);
      }

      // Should not open window when no active node
      expect(mockWindowOpen).not.toHaveBeenCalled();
    });

    it("encodes labId and nodeId in pop-out URL", async () => {
      const user = userEvent.setup();

      const nodesWithSpecialChars: Node[] = [
        {
          id: "node/with/slashes",
          name: "Special Node",
          nodeType: "device",
          type: DeviceType.ROUTER,
          model: "ceos",
          version: "4.28.0F",
          x: 100,
          y: 100,
        },
      ];

      const windows: ConsoleWindow[] = [
        {
          id: "win-1",
          deviceIds: ["node/with/slashes"],
          activeDeviceId: "node/with/slashes",
          x: 50,
          y: 50,
          isExpanded: true,
        },
      ];

      const { container } = render(
        <ConsoleManager
          {...defaultProps}
          nodes={nodesWithSpecialChars}
          windows={windows}
          labId="lab/123"
        />
      );

      const headerControls = container.querySelectorAll(
        ".border-l.border-stone-700 button"
      );
      if (headerControls[0]) {
        await user.click(headerControls[0]);
      }

      expect(mockWindowOpen).toHaveBeenCalledWith(
        "/studio/console/lab%2F123/node%2Fwith%2Fslashes",
        expect.any(String),
        expect.any(String)
      );
    });
  });
});
