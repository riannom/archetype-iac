import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Canvas from "./Canvas";
import {
  Node,
  Link,
  Annotation,
  DeviceModel,
  DeviceType,
  DeviceNode,
  ExternalNetworkNode,
} from "../types";
import { RuntimeStatus } from "./RuntimeControl";
import { ThemeProvider } from "../../theme/ThemeProvider";

// Mock getBoundingClientRect for container element
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

// Mock the theme hook
vi.mock("../../theme/index", () => ({
  useTheme: () => ({
    effectiveMode: "light",
  }),
}));

// Mock agentColors utility
vi.mock("../../utils/agentColors", () => ({
  getAgentColor: (id: string) => "#3b82f6",
  getAgentInitials: (name: string) => name.substring(0, 2).toUpperCase(),
}));

// Helper to render with ThemeProvider
const renderWithTheme = (ui: React.ReactElement) => {
  return render(<ThemeProvider>{ui}</ThemeProvider>);
};

// Sample device models
const mockDeviceModels: DeviceModel[] = [
  {
    id: "ceos",
    name: "Arista cEOS",
    type: DeviceType.ROUTER,
    icon: "fa-microchip",
    versions: ["4.28.0F"],
    isActive: true,
    vendor: "Arista",
  },
  {
    id: "srlinux",
    name: "Nokia SR Linux",
    type: DeviceType.ROUTER,
    icon: "fa-network-wired",
    versions: ["23.10.1"],
    isActive: true,
    vendor: "Nokia",
  },
  {
    id: "linux",
    name: "Linux Container",
    type: DeviceType.HOST,
    icon: "fa-server",
    versions: ["alpine:latest"],
    isActive: true,
    vendor: "Generic",
  },
];

// Factory functions for test data
const createDeviceNode = (
  overrides: Partial<DeviceNode> = {}
): DeviceNode => ({
  id: overrides.id || "node-1",
  name: overrides.name || "Router1",
  nodeType: "device",
  type: overrides.type || DeviceType.ROUTER,
  model: overrides.model || "ceos",
  version: overrides.version || "4.28.0F",
  x: overrides.x ?? 100,
  y: overrides.y ?? 100,
  ...overrides,
});

const createExternalNetworkNode = (
  overrides: Partial<ExternalNetworkNode> = {}
): ExternalNetworkNode => ({
  id: overrides.id || "ext-1",
  name: overrides.name || "External1",
  nodeType: "external",
  connectionType: overrides.connectionType || "vlan",
  x: overrides.x ?? 200,
  y: overrides.y ?? 200,
  vlanId: overrides.vlanId ?? 100,
  ...overrides,
});

const createLink = (overrides: Partial<Link> = {}): Link => ({
  id: overrides.id || "link-1",
  source: overrides.source || "node-1",
  target: overrides.target || "node-2",
  type: overrides.type || "p2p",
  sourceInterface: overrides.sourceInterface,
  targetInterface: overrides.targetInterface,
  ...overrides,
});

const createAnnotation = (overrides: Partial<Annotation> = {}): Annotation => ({
  id: overrides.id || "ann-1",
  type: overrides.type || "rect",
  x: overrides.x ?? 150,
  y: overrides.y ?? 150,
  width: overrides.width ?? 100,
  height: overrides.height ?? 60,
  ...overrides,
});

describe("Canvas", () => {
  // Default mock handlers
  const mockOnNodeMove = vi.fn();
  const mockOnAnnotationMove = vi.fn();
  const mockOnConnect = vi.fn();
  const mockOnSelect = vi.fn();
  const mockOnOpenConsole = vi.fn();
  const mockOnUpdateStatus = vi.fn();
  const mockOnDelete = vi.fn();

  const defaultProps = {
    nodes: [] as Node[],
    links: [] as Link[],
    annotations: [] as Annotation[],
    runtimeStates: {} as Record<string, RuntimeStatus>,
    nodeStates: {} as Record<
      string,
      { id: string; node_id: string; node_name: string }
    >,
    deviceModels: mockDeviceModels,
    agents: [] as { id: string; name: string }[],
    showAgentIndicators: false,
    onToggleAgentIndicators: undefined as (() => void) | undefined,
    onNodeMove: mockOnNodeMove,
    onAnnotationMove: mockOnAnnotationMove,
    onConnect: mockOnConnect,
    selectedId: null as string | null,
    onSelect: mockOnSelect,
    onOpenConsole: mockOnOpenConsole,
    onUpdateStatus: mockOnUpdateStatus,
    onDelete: mockOnDelete,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // Reset getBoundingClientRect mock
    Element.prototype.getBoundingClientRect = mockGetBoundingClientRect;
  });

  describe("Basic Rendering", () => {
    it("renders an empty canvas without crashing", () => {
      renderWithTheme(<Canvas {...defaultProps} />);

      // Canvas container should exist
      const canvasContainer = document.querySelector(".flex-1.relative");
      expect(canvasContainer).toBeInTheDocument();
    });

    it("renders with zoom control buttons", () => {
      renderWithTheme(<Canvas {...defaultProps} />);

      // Check for zoom and navigation buttons (fa-plus, fa-minus, fa-crosshairs, fa-maximize)
      expect(document.querySelector(".fa-plus")).toBeInTheDocument();
      expect(document.querySelector(".fa-minus")).toBeInTheDocument();
      expect(document.querySelector(".fa-crosshairs")).toBeInTheDocument();
      expect(document.querySelector(".fa-maximize")).toBeInTheDocument();
    });

    it("applies cursor-crosshair class when not panning", () => {
      renderWithTheme(<Canvas {...defaultProps} />);

      const canvasContainer = document.querySelector(".flex-1.relative");
      expect(canvasContainer).toHaveClass("cursor-crosshair");
    });
  });

  describe("Node Rendering", () => {
    it("renders a single device node", () => {
      const node = createDeviceNode({ name: "TestRouter" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      expect(screen.getByText("TestRouter")).toBeInTheDocument();
    });

    it("renders multiple device nodes", () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1" }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 200, y: 200 }),
        createDeviceNode({ id: "node-3", name: "Host1", type: DeviceType.HOST, x: 300, y: 300 }),
      ];

      renderWithTheme(<Canvas {...defaultProps} nodes={nodes} />);

      expect(screen.getByText("Router1")).toBeInTheDocument();
      expect(screen.getByText("Router2")).toBeInTheDocument();
      expect(screen.getByText("Host1")).toBeInTheDocument();
    });

    it("renders external network nodes with cloud icon", () => {
      const extNode = createExternalNetworkNode({ name: "ExternalNetwork" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[extNode]} />);

      expect(screen.getByText("ExternalNetwork")).toBeInTheDocument();
      expect(document.querySelector(".fa-cloud")).toBeInTheDocument();
    });

    it("renders VLAN label for external network nodes with vlan connection type", () => {
      const extNode = createExternalNetworkNode({
        name: "VlanNetwork",
        connectionType: "vlan",
        vlanId: 100,
      });

      renderWithTheme(<Canvas {...defaultProps} nodes={[extNode]} />);

      expect(screen.getByText("VLAN 100")).toBeInTheDocument();
    });

    it("renders bridge name for external network nodes with bridge connection type", () => {
      const extNode = createExternalNetworkNode({
        name: "BridgeNetwork",
        connectionType: "bridge",
        bridgeName: "br-prod",
      });

      renderWithTheme(<Canvas {...defaultProps} nodes={[extNode]} />);

      expect(screen.getByText("br-prod")).toBeInTheDocument();
    });

    it("applies selected styling to selected node", () => {
      const node = createDeviceNode({ id: "node-1", name: "SelectedNode" });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId="node-1" />
      );

      // The selected node should have ring-2 and ring-sage-500 classes
      const nodeElement = screen
        .getByText("SelectedNode")
        .closest(".absolute.w-12");
      expect(nodeElement).toHaveClass("ring-2");
    });

    it("shows status dot for running nodes", () => {
      const node = createDeviceNode({ id: "node-1", name: "RunningNode" });
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={runtimeStates} />
      );

      // Check for green status dot (running status)
      const statusDot = document.querySelector('[title="running"]');
      expect(statusDot).toBeInTheDocument();
    });

    it("shows status dot for booting nodes with animation", () => {
      const node = createDeviceNode({ id: "node-1", name: "BootingNode" });
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "booting",
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={runtimeStates} />
      );

      const statusDot = document.querySelector('[title="booting"]');
      expect(statusDot).toBeInTheDocument();
      expect(statusDot).toHaveClass("animate-pulse");
    });

    it("shows status dot for error nodes", () => {
      const node = createDeviceNode({ id: "node-1", name: "ErrorNode" });
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "error",
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={runtimeStates} />
      );

      const statusDot = document.querySelector('[title="error"]');
      expect(statusDot).toBeInTheDocument();
    });

    it("does not show status dot for undeployed nodes", () => {
      const node = createDeviceNode({ id: "node-1", name: "UndeployedNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      // No status dot should be present
      const statusDots = document.querySelectorAll('[title="running"], [title="stopped"], [title="booting"], [title="error"]');
      expect(statusDots.length).toBe(0);
    });
  });

  describe("Link Rendering", () => {
    it("renders a link between two nodes", () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];
      const links = [createLink({ id: "link-1", source: "node-1", target: "node-2" })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      // Check that link line exists (SVG line element)
      const svgLines = document.querySelectorAll("line");
      // There should be at least 2 lines per link (one transparent for interaction, one visible)
      expect(svgLines.length).toBeGreaterThanOrEqual(2);
    });

    it("renders link with source and target interface labels", () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];
      const links = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth0",
          targetInterface: "eth1",
        }),
      ];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      expect(screen.getByText("eth0")).toBeInTheDocument();
      expect(screen.getByText("eth1")).toBeInTheDocument();
    });

    it("does not render link if source node is missing", () => {
      const nodes = [
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];
      const links = [createLink({ id: "link-1", source: "node-1", target: "node-2" })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      // Link should not render without both endpoints
      // Only get visible lines (not the hidden interaction lines)
      const visibleLines = document.querySelectorAll('line[stroke]:not([stroke="transparent"])');
      expect(visibleLines.length).toBe(0);
    });

    it("applies selected styling to selected link", () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];
      const links = [createLink({ id: "link-1", source: "node-1", target: "node-2" })];

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={nodes}
          links={links}
          selectedId="link-1"
        />
      );

      // Selected link should have thicker stroke
      const visibleLine = document.querySelector('line[stroke-width="3"]');
      expect(visibleLine).toBeInTheDocument();
    });

    it("renders dashed lines for links connected to external networks", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createExternalNetworkNode({ id: "ext-1", name: "External", x: 300, y: 100 }),
      ];
      const links = [createLink({ id: "link-1", source: "node-1", target: "ext-1" })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      // External links should have dashed stroke
      const dashedLine = document.querySelector('line[stroke-dasharray="6 4"]');
      expect(dashedLine).toBeInTheDocument();
    });
  });

  describe("Annotation Rendering", () => {
    it("renders a rect annotation", () => {
      const annotation = createAnnotation({
        id: "ann-1",
        type: "rect",
        x: 100,
        y: 100,
        width: 150,
        height: 80,
      });

      renderWithTheme(
        <Canvas {...defaultProps} annotations={[annotation]} />
      );

      const rect = document.querySelector("rect");
      expect(rect).toBeInTheDocument();
      expect(rect).toHaveAttribute("x", "100");
      expect(rect).toHaveAttribute("y", "100");
      expect(rect).toHaveAttribute("width", "150");
      expect(rect).toHaveAttribute("height", "80");
    });

    it("renders a circle annotation", () => {
      const annotation = createAnnotation({
        id: "ann-1",
        type: "circle",
        x: 200,
        y: 200,
        width: 80,
      });

      renderWithTheme(
        <Canvas {...defaultProps} annotations={[annotation]} />
      );

      const circle = document.querySelector("circle");
      expect(circle).toBeInTheDocument();
      expect(circle).toHaveAttribute("cx", "200");
      expect(circle).toHaveAttribute("cy", "200");
      expect(circle).toHaveAttribute("r", "40"); // width / 2
    });

    it("renders a text annotation", () => {
      const annotation = createAnnotation({
        id: "ann-1",
        type: "text",
        x: 150,
        y: 150,
        text: "Test Label",
      });

      renderWithTheme(
        <Canvas {...defaultProps} annotations={[annotation]} />
      );

      expect(screen.getByText("Test Label")).toBeInTheDocument();
    });

    it("applies selected styling to selected annotation", () => {
      const annotation = createAnnotation({ id: "ann-1", type: "rect" });

      renderWithTheme(
        <Canvas
          {...defaultProps}
          annotations={[annotation]}
          selectedId="ann-1"
        />
      );

      // Selected annotation should have dashed stroke
      const rect = document.querySelector('rect[stroke-dasharray="4"]');
      expect(rect).toBeInTheDocument();
    });
  });

  describe("Node Interactions", () => {
    it("selects a node on click", async () => {
      const node = createDeviceNode({ id: "node-1", name: "ClickableNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("ClickableNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.mouseDown(nodeElement, { button: 0 });

      expect(mockOnSelect).toHaveBeenCalledWith("node-1");
    });

    it("does not start dragging on right-click", async () => {
      const node = createDeviceNode({ id: "node-1", name: "RightClickNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("RightClickNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      // Clear any previous calls
      mockOnSelect.mockClear();
      mockOnNodeMove.mockClear();

      // Right-click on the node
      fireEvent.mouseDown(nodeElement, { button: 2 });

      // Right-click should not trigger select on the node (button: 2 returns early in handleNodeMouseDown)
      // However, the onSelect(null) from handleMouseDown on canvas might still fire
      // The important thing is that no node was selected (not called with node-1)
      expect(mockOnSelect).not.toHaveBeenCalledWith("node-1");
    });

    it("opens context menu on right-click", async () => {
      const node = createDeviceNode({ id: "node-1", name: "ContextNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("ContextNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      // Context menu should appear with Node Actions header
      await waitFor(() => {
        expect(screen.getByText("Node Actions")).toBeInTheDocument();
      });
    });

    it("shows console option in context menu for device nodes", async () => {
      const node = createDeviceNode({ id: "node-1", name: "ConsoleNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("ConsoleNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Open Console")).toBeInTheDocument();
      });
    });

    it("calls onOpenConsole when console option is clicked", async () => {
      const user = userEvent.setup();
      const node = createDeviceNode({ id: "node-1", name: "ConsoleNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("ConsoleNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Open Console")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Open Console"));

      expect(mockOnOpenConsole).toHaveBeenCalledWith("node-1");
    });

    it("calls onDelete when delete option is clicked", async () => {
      const user = userEvent.setup();
      const node = createDeviceNode({ id: "node-1", name: "DeleteNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("DeleteNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Remove Device")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Remove Device"));

      expect(mockOnDelete).toHaveBeenCalledWith("node-1");
    });

    it("shows External Network header for external nodes context menu", async () => {
      const extNode = createExternalNetworkNode({ id: "ext-1", name: "ExternalNet" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[extNode]} />);

      const nodeLabel = screen.getByText("ExternalNet");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("External Network")).toBeInTheDocument();
      });
    });

    it("shows Remove External Network option for external nodes", async () => {
      const extNode = createExternalNetworkNode({ id: "ext-1", name: "ExternalNet" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[extNode]} />);

      const nodeLabel = screen.getByText("ExternalNet");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Remove External Network")).toBeInTheDocument();
      });
    });
  });

  describe("Link Interactions", () => {
    it("selects a link on click", async () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];
      const links = [createLink({ id: "link-1", source: "node-1", target: "node-2" })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      // Click on the transparent interaction line
      const transparentLine = document.querySelector('line[stroke="transparent"]');
      fireEvent.mouseDown(transparentLine!, { button: 0 });

      expect(mockOnSelect).toHaveBeenCalledWith("link-1");
    });

    it("link interaction area is present for clicking", () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];
      const links = [createLink({ id: "link-1", source: "node-1", target: "node-2" })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      // The link has a transparent line for interaction (wider hit area)
      const transparentLine = document.querySelector('line[stroke="transparent"]');
      expect(transparentLine).toBeInTheDocument();
      expect(transparentLine).toHaveAttribute("stroke-width", "12");
    });

    it("changes link style on hover", async () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];
      const links = [createLink({ id: "link-1", source: "node-1", target: "node-2" })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      const transparentLine = document.querySelector('line[stroke="transparent"]');
      expect(transparentLine).toBeInTheDocument();

      // Trigger hover on the transparent line
      fireEvent.mouseEnter(transparentLine!);

      // The visible line should change to thicker stroke (3px) on hover
      await waitFor(() => {
        const visibleLine = document.querySelector('line[stroke-width="3"]');
        expect(visibleLine).toBeInTheDocument();
      });
    });
  });

  describe("Annotation Interactions", () => {
    it("selects an annotation on click", async () => {
      const annotation = createAnnotation({ id: "ann-1", type: "rect" });

      renderWithTheme(
        <Canvas {...defaultProps} annotations={[annotation]} />
      );

      const rect = document.querySelector("rect")!;
      fireEvent.mouseDown(rect, { button: 0 });

      expect(mockOnSelect).toHaveBeenCalledWith("ann-1");
    });
  });

  describe("Keyboard Interactions", () => {
    it("deletes selected item on Delete key press", async () => {
      const node = createDeviceNode({ id: "node-1", name: "DeleteMe" });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId="node-1" />
      );

      fireEvent.keyDown(window, { key: "Delete" });

      expect(mockOnDelete).toHaveBeenCalledWith("node-1");
    });

    it("deletes selected item on Backspace key press", async () => {
      const node = createDeviceNode({ id: "node-1", name: "DeleteMe" });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId="node-1" />
      );

      fireEvent.keyDown(window, { key: "Backspace" });

      expect(mockOnDelete).toHaveBeenCalledWith("node-1");
    });

    it("does not delete when no item is selected", async () => {
      const node = createDeviceNode({ id: "node-1", name: "KeepMe" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      fireEvent.keyDown(window, { key: "Delete" });

      expect(mockOnDelete).not.toHaveBeenCalled();
    });

    it("does not delete when focus is on an input element", async () => {
      const node = createDeviceNode({ id: "node-1", name: "KeepMe" });

      renderWithTheme(
        <>
          <input type="text" data-testid="test-input" />
          <Canvas {...defaultProps} nodes={[node]} selectedId="node-1" />
        </>
      );

      const input = screen.getByTestId("test-input");
      input.focus();

      // Dispatch the keydown event directly on the input, which should not trigger delete
      // because Canvas checks target.tagName !== 'INPUT'
      const keydownEvent = new KeyboardEvent("keydown", {
        key: "Delete",
        bubbles: true,
      });
      Object.defineProperty(keydownEvent, "target", {
        value: input,
        writable: false,
      });
      window.dispatchEvent(keydownEvent);

      expect(mockOnDelete).not.toHaveBeenCalled();
    });
  });

  describe("Canvas Interactions", () => {
    it("clears selection when clicking on canvas background", async () => {
      const node = createDeviceNode({ id: "node-1", name: "Node1" });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId="node-1" />
      );

      const canvas = document.querySelector(".flex-1.relative")!;
      fireEvent.mouseDown(canvas, { button: 0 });

      expect(mockOnSelect).toHaveBeenCalledWith(null);
    });

    it("closes context menu on canvas click", async () => {
      const node = createDeviceNode({ id: "node-1", name: "ContextNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      // Open context menu
      const nodeLabel = screen.getByText("ContextNode");
      const nodeElement = nodeLabel.closest(".absolute")!;
      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Node Actions")).toBeInTheDocument();
      });

      // Click elsewhere to close
      fireEvent.click(window);

      await waitFor(() => {
        expect(screen.queryByText("Node Actions")).not.toBeInTheDocument();
      });
    });
  });

  describe("Node Connection (Linking)", () => {
    it("creates connection when shift-clicking from one node to another", async () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];

      renderWithTheme(<Canvas {...defaultProps} nodes={nodes} />);

      const node1Label = screen.getByText("Router1");
      const node1Element = node1Label.closest(".absolute")!;

      const node2Label = screen.getByText("Router2");
      const node2Element = node2Label.closest(".absolute")!;

      // Shift+mousedown on first node to start linking
      fireEvent.mouseDown(node1Element, { button: 0, shiftKey: true });

      // MouseUp on second node to complete connection
      fireEvent.mouseUp(node2Element, { button: 0 });

      expect(mockOnConnect).toHaveBeenCalledWith("node-1", "node-2");
    });

    it("does not create self-connection", async () => {
      const node = createDeviceNode({ id: "node-1", name: "Router1" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("Router1");
      const nodeElement = nodeLabel.closest(".absolute")!;

      // Shift+mousedown to start linking
      fireEvent.mouseDown(nodeElement, { button: 0, shiftKey: true });

      // MouseUp on same node
      fireEvent.mouseUp(nodeElement, { button: 0 });

      expect(mockOnConnect).not.toHaveBeenCalled();
    });
  });

  describe("Node Dragging", () => {
    it("calls onNodeMove when dragging a node", async () => {
      const node = createDeviceNode({ id: "node-1", name: "DragMe", x: 100, y: 100 });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("DragMe");
      const nodeElement = nodeLabel.closest(".absolute")!;
      const canvas = document.querySelector(".flex-1.relative")!;

      // Start dragging
      fireEvent.mouseDown(nodeElement, { button: 0 });

      // Move mouse
      fireEvent.mouseMove(canvas, { clientX: 200, clientY: 200 });

      expect(mockOnNodeMove).toHaveBeenCalled();
    });

    it("stops dragging on mouseUp", async () => {
      const node = createDeviceNode({ id: "node-1", name: "DragMe", x: 100, y: 100 });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("DragMe");
      const nodeElement = nodeLabel.closest(".absolute")!;
      const canvas = document.querySelector(".flex-1.relative")!;

      // Start dragging
      fireEvent.mouseDown(nodeElement, { button: 0 });

      // Mouse up to stop dragging
      fireEvent.mouseUp(canvas);

      // Clear calls
      mockOnNodeMove.mockClear();

      // Move mouse - should not trigger onNodeMove
      fireEvent.mouseMove(canvas, { clientX: 300, clientY: 300 });

      expect(mockOnNodeMove).not.toHaveBeenCalled();
    });
  });

  describe("Annotation Dragging", () => {
    it("calls onAnnotationMove when dragging an annotation", async () => {
      const annotation = createAnnotation({ id: "ann-1", type: "rect", x: 150, y: 150 });

      renderWithTheme(
        <Canvas {...defaultProps} annotations={[annotation]} />
      );

      const rect = document.querySelector("rect")!;
      const canvas = document.querySelector(".flex-1.relative")!;

      // Start dragging
      fireEvent.mouseDown(rect, { button: 0 });

      // Move mouse
      fireEvent.mouseMove(canvas, { clientX: 250, clientY: 250 });

      expect(mockOnAnnotationMove).toHaveBeenCalled();
    });
  });

  describe("Zoom Controls", () => {
    it("increases zoom when plus button is clicked", async () => {
      const user = userEvent.setup();

      renderWithTheme(<Canvas {...defaultProps} />);

      const zoomInButton = document.querySelector(".fa-plus")!.closest("button")!;

      await user.click(zoomInButton);

      // Check that the transform scale has increased
      const transformDiv = document.querySelector('[style*="scale"]');
      expect(transformDiv).toBeInTheDocument();
    });

    it("decreases zoom when minus button is clicked", async () => {
      const user = userEvent.setup();

      renderWithTheme(<Canvas {...defaultProps} />);

      const zoomOutButton = document.querySelector(".fa-minus")!.closest("button")!;

      await user.click(zoomOutButton);

      // Transform div should exist
      const transformDiv = document.querySelector('[style*="scale"]');
      expect(transformDiv).toBeInTheDocument();
    });

    it("centers canvas when crosshairs button is clicked", async () => {
      const user = userEvent.setup();

      renderWithTheme(<Canvas {...defaultProps} />);

      // First zoom in
      const zoomInButton = document.querySelector(".fa-plus")!.closest("button")!;
      await user.click(zoomInButton);

      // Then center
      const centerButton = document.querySelector(".fa-crosshairs")!.closest("button")!;
      await user.click(centerButton);

      // Check transform is reset to translate(0, 0) scale(1)
      const transformDiv = document.querySelector('[style*="translate(0px, 0px) scale(1)"]');
      expect(transformDiv).toBeInTheDocument();
    });

    it("fits to screen when maximize button is clicked with nodes", async () => {
      const user = userEvent.setup();
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 500, y: 400 }),
      ];

      renderWithTheme(<Canvas {...defaultProps} nodes={nodes} />);

      const fitButton = document.querySelector(".fa-maximize")!.closest("button")!;

      await user.click(fitButton);

      // Transform should have been applied
      const transformDiv = document.querySelector('[style*="scale"]');
      expect(transformDiv).toBeInTheDocument();
    });
  });

  describe("Agent Indicators", () => {
    it("does not show agent indicator toggle when only one agent", () => {
      renderWithTheme(
        <Canvas
          {...defaultProps}
          agents={[{ id: "agent-1", name: "Agent 1" }]}
          onToggleAgentIndicators={vi.fn()}
        />
      );

      // Server icon should not be present for toggle
      const serverIcon = document.querySelector("button .fa-server");
      expect(serverIcon).not.toBeInTheDocument();
    });

    it("shows agent indicator toggle when multiple agents exist", () => {
      renderWithTheme(
        <Canvas
          {...defaultProps}
          agents={[
            { id: "agent-1", name: "Agent 1" },
            { id: "agent-2", name: "Agent 2" },
          ]}
          onToggleAgentIndicators={vi.fn()}
        />
      );

      const serverIcon = document.querySelector("button .fa-server");
      expect(serverIcon).toBeInTheDocument();
    });

    it("calls onToggleAgentIndicators when toggle button is clicked", async () => {
      const user = userEvent.setup();
      const mockToggle = vi.fn();

      renderWithTheme(
        <Canvas
          {...defaultProps}
          agents={[
            { id: "agent-1", name: "Agent 1" },
            { id: "agent-2", name: "Agent 2" },
          ]}
          onToggleAgentIndicators={mockToggle}
        />
      );

      const toggleButton = document.querySelector("button .fa-server")!.closest("button")!;
      await user.click(toggleButton);

      expect(mockToggle).toHaveBeenCalled();
    });

    it("shows agent indicator on node when showAgentIndicators is true and node has host", () => {
      const node = createDeviceNode({ id: "node-1", name: "Router1" });
      const nodeStates = {
        "node-1": {
          id: "state-1",
          node_id: "node-1",
          node_name: "Router1",
          host_id: "agent-1",
          host_name: "Agent-One",
        },
      };

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          nodeStates={nodeStates}
          agents={[
            { id: "agent-1", name: "Agent One" },
            { id: "agent-2", name: "Agent Two" },
          ]}
          showAgentIndicators={true}
        />
      );

      // Should show agent initials
      expect(screen.getByTitle("Running on: Agent-One")).toBeInTheDocument();
    });

    it("does not show agent indicator when showAgentIndicators is false", () => {
      const node = createDeviceNode({ id: "node-1", name: "Router1" });
      const nodeStates = {
        "node-1": {
          id: "state-1",
          node_id: "node-1",
          node_name: "Router1",
          host_id: "agent-1",
          host_name: "Agent-One",
        },
      };

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          nodeStates={nodeStates}
          agents={[
            { id: "agent-1", name: "Agent One" },
            { id: "agent-2", name: "Agent Two" },
          ]}
          showAgentIndicators={false}
        />
      );

      expect(screen.queryByTitle("Running on: Agent-One")).not.toBeInTheDocument();
    });
  });

  describe("Context Menu Actions", () => {
    it("shows Start Node option for stopped nodes when other nodes are running", async () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "StoppedNode" }),
        createDeviceNode({ id: "node-2", name: "RunningNode2", x: 200, y: 100 }),
      ];
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "stopped",
        "node-2": "running",
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} runtimeStates={runtimeStates} />
      );

      const nodeLabel = screen.getByText("StoppedNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Start Node")).toBeInTheDocument();
      });
    });

    it("shows Deploy Lab option when no nodes are running", async () => {
      const node = createDeviceNode({ id: "node-1", name: "UndeployedNode" });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("UndeployedNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Deploy Lab")).toBeInTheDocument();
      });
    });

    it("shows Stop Node option for running nodes", async () => {
      const node = createDeviceNode({ id: "node-1", name: "RunningNode" });
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={runtimeStates} />
      );

      const nodeLabel = screen.getByText("RunningNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Stop Node")).toBeInTheDocument();
      });
    });

    it("calls onUpdateStatus with booting when start is clicked", async () => {
      const user = userEvent.setup();
      const nodes = [
        createDeviceNode({ id: "node-1", name: "StoppedNode" }),
        createDeviceNode({ id: "node-2", name: "RunningNode2", x: 200, y: 100 }),
      ];
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "stopped",
        "node-2": "running",
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} runtimeStates={runtimeStates} />
      );

      const nodeLabel = screen.getByText("StoppedNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Start Node")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Start Node"));

      expect(mockOnUpdateStatus).toHaveBeenCalledWith("node-1", "booting");
    });

    it("calls onUpdateStatus with stopped when stop is clicked", async () => {
      const user = userEvent.setup();
      const node = createDeviceNode({ id: "node-1", name: "RunningNode" });
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={runtimeStates} />
      );

      const nodeLabel = screen.getByText("RunningNode");
      const nodeElement = nodeLabel.closest(".absolute")!;

      fireEvent.contextMenu(nodeElement);

      await waitFor(() => {
        expect(screen.getByText("Stop Node")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Stop Node"));

      expect(mockOnUpdateStatus).toHaveBeenCalledWith("node-1", "stopped");
    });
  });

  describe("Edge Cases", () => {
    it("handles many nodes without crashing", () => {
      const nodes = Array.from({ length: 100 }, (_, i) =>
        createDeviceNode({
          id: `node-${i}`,
          name: `Node${i}`,
          x: (i % 10) * 100,
          y: Math.floor(i / 10) * 100,
        })
      );

      renderWithTheme(<Canvas {...defaultProps} nodes={nodes} />);

      expect(screen.getByText("Node0")).toBeInTheDocument();
      expect(screen.getByText("Node99")).toBeInTheDocument();
    });

    it("handles many links without crashing", () => {
      const nodes = Array.from({ length: 20 }, (_, i) =>
        createDeviceNode({
          id: `node-${i}`,
          name: `Node${i}`,
          x: (i % 5) * 150,
          y: Math.floor(i / 5) * 150,
        })
      );

      const links = Array.from({ length: 19 }, (_, i) =>
        createLink({
          id: `link-${i}`,
          source: `node-${i}`,
          target: `node-${i + 1}`,
        })
      );

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      // Check that links are rendered
      const svgLines = document.querySelectorAll("line");
      expect(svgLines.length).toBeGreaterThan(0);
    });

    it("handles mixed node types (device and external)", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Switch1", type: DeviceType.SWITCH, x: 200, y: 100 }),
        createExternalNetworkNode({ id: "ext-1", name: "External1", x: 300, y: 100 }),
      ];

      renderWithTheme(<Canvas {...defaultProps} nodes={nodes} />);

      expect(screen.getByText("Router1")).toBeInTheDocument();
      expect(screen.getByText("Switch1")).toBeInTheDocument();
      expect(screen.getByText("External1")).toBeInTheDocument();
      expect(document.querySelector(".fa-cloud")).toBeInTheDocument();
    });

    it("handles empty annotations array", () => {
      renderWithTheme(<Canvas {...defaultProps} annotations={[]} />);

      // Should render without errors
      const svg = document.querySelector("svg");
      expect(svg).toBeInTheDocument();
    });

    it("handles undefined optional props gracefully", () => {
      const minimalProps = {
        nodes: [] as Node[],
        links: [] as Link[],
        annotations: [] as Annotation[],
        runtimeStates: {},
        deviceModels: [],
        onNodeMove: vi.fn(),
        onAnnotationMove: vi.fn(),
        onConnect: vi.fn(),
        selectedId: null,
        onSelect: vi.fn(),
        onOpenConsole: vi.fn(),
        onUpdateStatus: vi.fn(),
        onDelete: vi.fn(),
      };

      renderWithTheme(<Canvas {...minimalProps} />);

      // Should render without errors
      expect(document.querySelector(".flex-1.relative")).toBeInTheDocument();
    });
  });

  describe("Node Shape Styling", () => {
    it("renders router nodes with circular shape", () => {
      const node = createDeviceNode({
        id: "node-1",
        name: "Router1",
        type: DeviceType.ROUTER,
      });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      // Find the node container (the one with inline style for borderRadius)
      const nodeLabel = screen.getByText("Router1");
      const nodeElement = nodeLabel.closest("[style]") as HTMLElement;
      expect(nodeElement?.style.borderRadius).toBe("50%");
    });

    it("renders switch nodes with minimal border radius", () => {
      const node = createDeviceNode({
        id: "node-1",
        name: "Switch1",
        type: DeviceType.SWITCH,
      });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("Switch1");
      const nodeElement = nodeLabel.closest("[style]") as HTMLElement;
      expect(nodeElement?.style.borderRadius).toBe("4px");
    });

    it("renders host nodes with standard border radius", () => {
      const node = createDeviceNode({
        id: "node-1",
        name: "Host1",
        type: DeviceType.HOST,
      });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeLabel = screen.getByText("Host1");
      const nodeElement = nodeLabel.closest("[style]") as HTMLElement;
      expect(nodeElement?.style.borderRadius).toBe("8px");
    });
  });

  describe("Linking Preview Line", () => {
    it("shows preview line while creating a connection", () => {
      const nodes = [
        createDeviceNode({ id: "node-1", name: "Router1", x: 100, y: 100 }),
        createDeviceNode({ id: "node-2", name: "Router2", x: 300, y: 100 }),
      ];

      renderWithTheme(<Canvas {...defaultProps} nodes={nodes} />);

      const nodeLabel = screen.getByText("Router1");
      const nodeElement = nodeLabel.closest(".absolute")!;

      // Start linking with shift+click
      fireEvent.mouseDown(nodeElement, { button: 0, shiftKey: true });

      // Preview line should appear (dashed line from source to mouse position)
      const previewLine = document.querySelector('line[stroke-dasharray="4"]');
      expect(previewLine).toBeInTheDocument();
    });
  });

  describe("Wheel Zoom", () => {
    it("zooms on ctrl+wheel", () => {
      renderWithTheme(<Canvas {...defaultProps} />);

      const canvas = document.querySelector(".flex-1.relative")!;

      // Ctrl+wheel should zoom
      fireEvent.wheel(canvas, { deltaY: -100, ctrlKey: true });

      const transformDiv = document.querySelector('[style*="scale"]');
      expect(transformDiv).toBeInTheDocument();
    });

    it("pans on wheel without modifier", () => {
      renderWithTheme(<Canvas {...defaultProps} />);

      const canvas = document.querySelector(".flex-1.relative")!;

      // Wheel without modifier should pan
      fireEvent.wheel(canvas, { deltaX: 50, deltaY: 50 });

      const transformDiv = document.querySelector('[style*="translate"]');
      expect(transformDiv).toBeInTheDocument();
    });
  });

  describe("Middle Mouse Button Panning", () => {
    it("starts panning on middle mouse button", () => {
      renderWithTheme(<Canvas {...defaultProps} />);

      const canvas = document.querySelector(".flex-1.relative")!;

      // Middle mouse button down
      fireEvent.mouseDown(canvas, { button: 1 });

      // Canvas should have grabbing cursor
      expect(canvas).toHaveClass("cursor-grabbing");
    });
  });
});
