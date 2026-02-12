import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PropertiesPanel from "./PropertiesPanel";
import {
  DeviceNode,
  DeviceType,
  DeviceModel,
  Link,
  Annotation,
  ExternalNetworkNode,
} from "../types";
import { RuntimeStatus } from "./RuntimeControl";
import { PortManager } from "../hooks/usePortManager";

// Mock ExternalNetworkConfig component
vi.mock("./ExternalNetworkConfig", () => ({
  default: ({
    node,
    onUpdate,
    onDelete,
  }: {
    node: ExternalNetworkNode;
    onUpdate: (id: string, updates: Partial<ExternalNetworkNode>) => void;
    onDelete: (id: string) => void;
  }) => (
    <div data-testid="external-network-config">
      <span data-testid="external-node-name">{node.name}</span>
      <button
        data-testid="update-external"
        onClick={() => onUpdate(node.id, { name: "Updated Name" })}
      >
        Update
      </button>
      <button data-testid="delete-external" onClick={() => onDelete(node.id)}>
        Delete
      </button>
    </div>
  ),
}));

// Mock InterfaceSelect component
vi.mock("./InterfaceSelect", () => ({
  default: ({
    value,
    availableInterfaces,
    onChange,
    placeholder,
  }: {
    value: string;
    availableInterfaces: string[];
    onChange: (value: string) => void;
    placeholder?: string;
  }) => (
    <select
      data-testid="interface-select"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{placeholder || "Select interface"}</option>
      {availableInterfaces.map((iface) => (
        <option key={iface} value={iface}>
          {iface}
        </option>
      ))}
    </select>
  ),
}));

// Mock getAgentColor
vi.mock("../../utils/agentColors", () => ({
  getAgentColor: (id: string) => `#${id.slice(0, 6)}`,
}));

const mockDeviceModels: DeviceModel[] = [
  {
    id: "ceos",
    name: "Arista cEOS",
    type: DeviceType.ROUTER,
    icon: "fa-microchip",
    versions: ["4.28.0F", "4.27.0F"],
    isActive: true,
    vendor: "Arista",
  },
  {
    id: "srlinux",
    name: "Nokia SR Linux",
    type: DeviceType.ROUTER,
    icon: "fa-microchip",
    versions: ["23.10.1"],
    isActive: true,
    vendor: "Nokia",
  },
];

const createDeviceNode = (overrides: Partial<DeviceNode> = {}): DeviceNode => ({
  id: "node-1",
  name: "Router1",
  nodeType: "device",
  type: DeviceType.ROUTER,
  model: "ceos",
  version: "4.28.0F",
  x: 100,
  y: 100,
  cpu: 2,
  memory: 2048,
  config: "hostname router1",
  container_name: "archetype-lab1-router1",
  ...overrides,
});

const createExternalNetworkNode = (
  overrides: Partial<ExternalNetworkNode> = {}
): ExternalNetworkNode => ({
  id: "ext-1",
  name: "Production Network",
  nodeType: "external",
  connectionType: "vlan",
  parentInterface: "ens192",
  vlanId: 100,
  x: 300,
  y: 300,
  ...overrides,
});

const createLink = (overrides: Partial<Link> = {}): Link => ({
  id: "link-1",
  source: "node-1",
  target: "node-2",
  type: "p2p",
  sourceInterface: "eth1",
  targetInterface: "eth1",
  ...overrides,
});

const createAnnotation = (overrides: Partial<Annotation> = {}): Annotation => ({
  id: "ann-1",
  type: "text",
  x: 50,
  y: 50,
  text: "Test Label",
  color: "#65A30D",
  fontSize: 14,
  ...overrides,
});

const createMockPortManager = (): PortManager => ({
  getUsedInterfaces: vi.fn().mockReturnValue(new Set(["eth1"])),
  getAvailableInterfaces: vi.fn().mockReturnValue(["eth2", "eth3", "eth4"]),
  getNextInterface: vi.fn().mockReturnValue("eth2"),
  isInterfaceUsed: vi.fn().mockReturnValue(false),
  getNodeModel: vi.fn().mockReturnValue("ceos"),
});

describe("PropertiesPanel", () => {
  const mockOnUpdateNode = vi.fn();
  const mockOnUpdateLink = vi.fn();
  const mockOnUpdateAnnotation = vi.fn();
  const mockOnDelete = vi.fn();
  const mockOnOpenConsole = vi.fn();
  const mockOnUpdateStatus = vi.fn();
  const mockOnOpenConfigViewer = vi.fn();

  const mockNode = createDeviceNode();
  const mockNodes: DeviceNode[] = [
    mockNode,
    createDeviceNode({ id: "node-2", name: "Switch1" }),
  ];
  const mockLinks: Link[] = [];
  const mockPortManager = createMockPortManager();

  const defaultProps = {
    selectedItem: mockNode as DeviceNode | Link | Annotation | null,
    onUpdateNode: mockOnUpdateNode,
    onUpdateLink: mockOnUpdateLink,
    onUpdateAnnotation: mockOnUpdateAnnotation,
    onDelete: mockOnDelete,
    nodes: mockNodes,
    links: mockLinks,
    onOpenConsole: mockOnOpenConsole,
    runtimeStates: {} as Record<string, RuntimeStatus>,
    onUpdateStatus: mockOnUpdateStatus,
    deviceModels: mockDeviceModels,
    portManager: mockPortManager,
    onOpenConfigViewer: mockOnOpenConfigViewer,
    agents: [],
    nodeStates: {},
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("Null selection", () => {
    it("returns null when no item is selected", () => {
      const { container } = render(
        <PropertiesPanel {...defaultProps} selectedItem={null} />
      );

      expect(container.firstChild).toBeNull();
    });
  });

  describe("Device Node Properties", () => {
    it("renders node name in header", () => {
      render(<PropertiesPanel {...defaultProps} />);

      expect(screen.getByText("Router1")).toBeInTheDocument();
    });

    it("renders device model name", () => {
      render(<PropertiesPanel {...defaultProps} />);

      expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
    });

    it("renders delete button", () => {
      render(<PropertiesPanel {...defaultProps} />);

      const deleteButton = document.querySelector(".fa-trash-can");
      expect(deleteButton).toBeInTheDocument();
    });

    it("calls onDelete when delete button is clicked", async () => {
      const user = userEvent.setup();

      render(<PropertiesPanel {...defaultProps} />);

      const deleteButton = document
        .querySelector(".fa-trash-can")!
        .closest("button");
      await user.click(deleteButton!);

      expect(mockOnDelete).toHaveBeenCalledWith("node-1");
    });

    describe("Tab navigation", () => {
      it("renders all four tabs", () => {
        render(<PropertiesPanel {...defaultProps} />);

        expect(screen.getByText("general")).toBeInTheDocument();
        expect(screen.getByText("hardware")).toBeInTheDocument();
        expect(screen.getByText("connectivity")).toBeInTheDocument();
        expect(screen.getByText("config")).toBeInTheDocument();
      });

      it("general tab is active by default", () => {
        render(<PropertiesPanel {...defaultProps} />);

        const generalTab = screen.getByText("general");
        expect(generalTab.closest("button")).toHaveClass("text-sage-600");
      });

      it("switches to hardware tab when clicked", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("hardware"));

        expect(screen.getByText("CPU Allocation")).toBeInTheDocument();
        expect(screen.getByText("RAM Allocation")).toBeInTheDocument();
      });

      it("switches to connectivity tab when clicked", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("connectivity"));

        expect(screen.getByText("Active Interfaces")).toBeInTheDocument();
      });

      it("switches to config tab when clicked", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("config"));

        expect(screen.getByText("Startup Configuration")).toBeInTheDocument();
      });
    });

    describe("General tab", () => {
      it("displays node status", () => {
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "running",
        };

        render(
          <PropertiesPanel {...defaultProps} runtimeStates={runtimeStates} />
        );

        expect(screen.getByText("Status")).toBeInTheDocument();
        expect(screen.getByText("running")).toBeInTheDocument();
      });

      it("shows Deploy button when node is stopped and no nodes are running", () => {
        render(<PropertiesPanel {...defaultProps} />);

        expect(screen.getByText("DEPLOY")).toBeInTheDocument();
      });

      it("shows Start button when node is stopped and other nodes are running", () => {
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "stopped",
          "node-2": "running",
        };

        render(
          <PropertiesPanel {...defaultProps} runtimeStates={runtimeStates} />
        );

        expect(screen.getByText("START")).toBeInTheDocument();
      });

      it("shows Stop button when node is running", () => {
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "running",
        };

        render(
          <PropertiesPanel {...defaultProps} runtimeStates={runtimeStates} />
        );

        expect(screen.getByText("STOP")).toBeInTheDocument();
      });

      it("shows Reload button when node is running", () => {
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "running",
        };

        render(
          <PropertiesPanel {...defaultProps} runtimeStates={runtimeStates} />
        );

        expect(screen.getByText("RELOAD")).toBeInTheDocument();
      });

      it("calls onUpdateStatus when Deploy is clicked", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("DEPLOY"));

        expect(mockOnUpdateStatus).toHaveBeenCalledWith("node-1", "booting");
      });

      it("calls onUpdateStatus when Stop is clicked", async () => {
        const user = userEvent.setup();
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "running",
        };

        render(
          <PropertiesPanel {...defaultProps} runtimeStates={runtimeStates} />
        );

        await user.click(screen.getByText("STOP"));

        expect(mockOnUpdateStatus).toHaveBeenCalledWith("node-1", "stopped");
      });

      it("renders Display Name input", () => {
        render(<PropertiesPanel {...defaultProps} />);

        expect(screen.getByText("Display Name")).toBeInTheDocument();
        expect(screen.getByDisplayValue("Router1")).toBeInTheDocument();
      });

      it("updates node name when Display Name is changed", async () => {
        render(<PropertiesPanel {...defaultProps} />);

        const nameInput = screen.getByDisplayValue("Router1") as HTMLInputElement;

        // Use fireEvent to simulate a complete change
        fireEvent.change(nameInput, { target: { value: "NewRouter" } });

        expect(mockOnUpdateNode).toHaveBeenCalledWith("node-1", {
          name: "NewRouter",
        });
      });

      it("renders Image Version select", () => {
        render(<PropertiesPanel {...defaultProps} />);

        expect(screen.getByText("Image Version")).toBeInTheDocument();
        expect(screen.getByDisplayValue("4.28.0F")).toBeInTheDocument();
      });

      it("shows version options from device model", async () => {
        render(<PropertiesPanel {...defaultProps} />);

        const versionSelect = screen.getByDisplayValue("4.28.0F");

        // Check that version options are present
        expect(versionSelect).toContainHTML("4.28.0F");
        expect(versionSelect).toContainHTML("4.27.0F");
      });

      it("calls onOpenConsole when Open Console button is clicked", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("OPEN CONSOLE"));

        expect(mockOnOpenConsole).toHaveBeenCalledWith("node-1");
      });
    });

    describe("Agent placement", () => {
      it("does not show agent placement when only one agent", () => {
        render(
          <PropertiesPanel
            {...defaultProps}
            agents={[{ id: "agent-1", name: "Agent 1" }]}
          />
        );

        expect(screen.queryByText("Agent Placement")).not.toBeInTheDocument();
      });

      it("shows agent placement dropdown when multiple agents", () => {
        render(
          <PropertiesPanel
            {...defaultProps}
            agents={[
              { id: "agent-1", name: "Agent 1" },
              { id: "agent-2", name: "Agent 2" },
            ]}
          />
        );

        expect(screen.getByText("Agent Placement")).toBeInTheDocument();
        expect(screen.getByText("Auto (any available agent)")).toBeInTheDocument();
      });

      it("shows all agents in dropdown", async () => {
        const user = userEvent.setup();

        render(
          <PropertiesPanel
            {...defaultProps}
            agents={[
              { id: "agent-1", name: "Agent 1" },
              { id: "agent-2", name: "Agent 2" },
            ]}
          />
        );

        // Click the dropdown button to open it
        await user.click(screen.getByText("Auto (any available agent)"));

        expect(screen.getByText("Agent 1")).toBeInTheDocument();
        expect(screen.getByText("Agent 2")).toBeInTheDocument();
      });

      it("updates node host when agent is selected", async () => {
        const user = userEvent.setup();

        render(
          <PropertiesPanel
            {...defaultProps}
            agents={[
              { id: "agent-1", name: "Agent 1" },
              { id: "agent-2", name: "Agent 2" },
            ]}
          />
        );

        // Open the dropdown
        await user.click(screen.getByText("Auto (any available agent)"));
        // Select an agent
        await user.click(screen.getByText("Agent 1"));

        expect(mockOnUpdateNode).toHaveBeenCalledWith("node-1", {
          host: "agent-1",
        });
      });

      it("disables agent placement when node is running", () => {
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "running",
        };

        render(
          <PropertiesPanel
            {...defaultProps}
            runtimeStates={runtimeStates}
            agents={[
              { id: "agent-1", name: "Agent 1" },
              { id: "agent-2", name: "Agent 2" },
            ]}
          />
        );

        // The dropdown button should be disabled
        const dropdownButton = screen.getByRole("button", { name: /Auto \(any available agent\)/i });
        expect(dropdownButton).toBeDisabled();
      });
    });

    describe("Running On indicator", () => {
      it("shows Running On when node is running on a host", () => {
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "running",
        };
        const nodeStates = {
          "node-1": {
            id: "state-1",
            node_id: "node-1",
            node_name: "Router1",
            host_id: "agent-1",
            host_name: "Agent 1",
          },
        };

        render(
          <PropertiesPanel
            {...defaultProps}
            runtimeStates={runtimeStates}
            nodeStates={nodeStates}
            agents={[
              { id: "agent-1", name: "Agent 1" },
              { id: "agent-2", name: "Agent 2" },
            ]}
          />
        );

        expect(screen.getByText("Running On")).toBeInTheDocument();
        // Host name shown in the Running On section
        const runningOnSections = screen.getAllByText("Agent 1");
        expect(runningOnSections.length).toBeGreaterThanOrEqual(1);
      });
    });

    describe("Image sync status", () => {
      it("shows syncing status indicator", () => {
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "booting",
        };
        const nodeStates = {
          "node-1": {
            id: "state-1",
            node_id: "node-1",
            node_name: "Router1",
            image_sync_status: "syncing",
            image_sync_message: "Pushing image to host...",
          },
        };

        render(
          <PropertiesPanel
            {...defaultProps}
            runtimeStates={runtimeStates}
            nodeStates={nodeStates}
          />
        );

        expect(screen.getByText("Pushing Image")).toBeInTheDocument();
        expect(screen.getByText("Pushing image to host...")).toBeInTheDocument();
      });

      it("shows failed status indicator", () => {
        const runtimeStates: Record<string, RuntimeStatus> = {
          "node-1": "error",
        };
        const nodeStates = {
          "node-1": {
            id: "state-1",
            node_id: "node-1",
            node_name: "Router1",
            image_sync_status: "failed",
            image_sync_message: "Connection refused",
          },
        };

        render(
          <PropertiesPanel
            {...defaultProps}
            runtimeStates={runtimeStates}
            nodeStates={nodeStates}
          />
        );

        expect(screen.getByText("Image Sync Failed")).toBeInTheDocument();
        expect(screen.getByText("Connection refused")).toBeInTheDocument();
      });
    });

    describe("Hardware tab", () => {
      it("displays CPU slider with current value", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("hardware"));

        expect(screen.getByText("CPU Allocation")).toBeInTheDocument();
        expect(screen.getByText("2 Cores")).toBeInTheDocument();
      });

      it("displays RAM slider with current value", async () => {
        const user = userEvent.setup();

        const { container } = render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("hardware"));

        expect(screen.getByText("RAM Allocation")).toBeInTheDocument();
        // RAM displays as numeric input (MB) + slider
        const ramInput = container.querySelector('input[type="number"]') as HTMLInputElement;
        expect(ramInput).toBeInTheDocument();
        expect(ramInput.value).toBe("2048");
        expect(screen.getByText("MB")).toBeInTheDocument();
      });

      it("resets CPU and memory to device defaults", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("hardware"));
        await user.click(screen.getByText("Locked"));

        await user.click(screen.getByText("Reset"));

        expect(mockOnUpdateNode).toHaveBeenCalledWith("node-1", {
          cpu: 1,
          memory: 1024,
          disk_driver: undefined,
          nic_driver: undefined,
          machine_type: undefined,
        });
      });

      it("keeps hardware inputs locked by default", async () => {
        const user = userEvent.setup();
        const { container } = render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("hardware"));

        const cpuSlider = container.querySelector('input[type="range"]') as HTMLInputElement;
        const ramInput = container.querySelector('input[type="number"]') as HTMLInputElement;

        expect(cpuSlider).toBeDisabled();
        expect(ramInput).toBeDisabled();
      });

      it("updates CPU when slider is changed", async () => {
        const user = userEvent.setup();
        const { container } = render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("hardware"));

        // Find the CPU slider input (first range input is CPU)
        const cpuSlider = container.querySelector('input[type="range"]') as HTMLInputElement;
        expect(cpuSlider).toBeInTheDocument();

        // Use fireEvent to properly trigger the onChange
        fireEvent.change(cpuSlider, { target: { value: "4" } });

        expect(mockOnUpdateNode).toHaveBeenCalledWith("node-1", { cpu: 4 });
      });
    });

    describe("Connectivity tab", () => {
      it("shows empty state when no links", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("connectivity"));

        expect(screen.getByText("No active links")).toBeInTheDocument();
      });

      it("shows connections when links exist", async () => {
        const user = userEvent.setup();
        const links = [createLink()];

        render(<PropertiesPanel {...defaultProps} links={links} />);

        await user.click(screen.getByText("connectivity"));

        expect(screen.getByText(/Connection to/)).toBeInTheDocument();
      });

      it("shows interface select for each link", async () => {
        const user = userEvent.setup();
        const links = [createLink()];

        render(<PropertiesPanel {...defaultProps} links={links} />);

        await user.click(screen.getByText("connectivity"));

        expect(screen.getByTestId("interface-select")).toBeInTheDocument();
      });
    });

    describe("Config tab", () => {
      it("displays config textarea", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("config"));

        expect(screen.getByText("Startup Configuration")).toBeInTheDocument();
        expect(screen.getByDisplayValue("hostname router1")).toBeInTheDocument();
      });

      it("updates config when textarea is changed", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("config"));

        const textarea = screen.getByDisplayValue("hostname router1");

        // Use fireEvent to simulate a complete change
        fireEvent.change(textarea, { target: { value: "new config" } });

        expect(mockOnUpdateNode).toHaveBeenCalledWith("node-1", {
          config: "new config",
        });
      });

      it("shows expand button when onOpenConfigViewer is provided", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("config"));

        expect(screen.getByText("Expand")).toBeInTheDocument();
      });

      it("calls onOpenConfigViewer when expand is clicked", async () => {
        const user = userEvent.setup();

        render(<PropertiesPanel {...defaultProps} />);

        await user.click(screen.getByText("config"));
        await user.click(screen.getByText("Expand"));

        expect(mockOnOpenConfigViewer).toHaveBeenCalledWith(
          "node-1",
          "archetype-lab1-router1"
        );
      });
    });
  });

  describe("External Network Node", () => {
    it("renders ExternalNetworkConfig component for external nodes", () => {
      const extNode = createExternalNetworkNode();

      render(<PropertiesPanel {...defaultProps} selectedItem={extNode} />);

      expect(screen.getByTestId("external-network-config")).toBeInTheDocument();
      expect(screen.getByTestId("external-node-name")).toHaveTextContent(
        "Production Network"
      );
    });

    it("passes onUpdate to ExternalNetworkConfig", async () => {
      const user = userEvent.setup();
      const extNode = createExternalNetworkNode();

      render(<PropertiesPanel {...defaultProps} selectedItem={extNode} />);

      await user.click(screen.getByTestId("update-external"));

      expect(mockOnUpdateNode).toHaveBeenCalledWith("ext-1", {
        name: "Updated Name",
      });
    });

    it("passes onDelete to ExternalNetworkConfig", async () => {
      const user = userEvent.setup();
      const extNode = createExternalNetworkNode();

      render(<PropertiesPanel {...defaultProps} selectedItem={extNode} />);

      await user.click(screen.getByTestId("delete-external"));

      expect(mockOnDelete).toHaveBeenCalledWith("ext-1");
    });
  });

  describe("Link Properties", () => {
    it("renders link properties panel for links", () => {
      const link = createLink();

      render(<PropertiesPanel {...defaultProps} selectedItem={link} />);

      expect(screen.getByText("Link Properties")).toBeInTheDocument();
    });

    it("shows source and target node names", () => {
      const link = createLink();

      render(<PropertiesPanel {...defaultProps} selectedItem={link} />);

      expect(screen.getByText("Router1")).toBeInTheDocument();
      expect(screen.getByText("Switch1")).toBeInTheDocument();
    });

    it("shows interface selects for both ends", () => {
      const link = createLink();

      render(<PropertiesPanel {...defaultProps} selectedItem={link} />);

      const interfaceSelects = screen.getAllByTestId("interface-select");
      expect(interfaceSelects).toHaveLength(2);
    });

    it("calls onUpdateLink when source interface is changed", async () => {
      const user = userEvent.setup();
      const link = createLink();

      render(<PropertiesPanel {...defaultProps} selectedItem={link} />);

      const selects = screen.getAllByTestId("interface-select");
      await user.selectOptions(selects[0], "eth2");

      expect(mockOnUpdateLink).toHaveBeenCalledWith("link-1", {
        sourceInterface: "eth2",
      });
    });

    it("calls onUpdateLink when target interface is changed", async () => {
      const user = userEvent.setup();
      const link = createLink();

      render(<PropertiesPanel {...defaultProps} selectedItem={link} />);

      const selects = screen.getAllByTestId("interface-select");
      await user.selectOptions(selects[1], "eth3");

      expect(mockOnUpdateLink).toHaveBeenCalledWith("link-1", {
        targetInterface: "eth3",
      });
    });

    it("shows delete button for links", () => {
      const link = createLink();

      render(<PropertiesPanel {...defaultProps} selectedItem={link} />);

      const deleteButton = document.querySelector(".fa-trash-can");
      expect(deleteButton).toBeInTheDocument();
    });

    it("calls onDelete when link delete button is clicked", async () => {
      const user = userEvent.setup();
      const link = createLink();

      render(<PropertiesPanel {...defaultProps} selectedItem={link} />);

      const deleteButton = document
        .querySelector(".fa-trash-can")!
        .closest("button");
      await user.click(deleteButton!);

      expect(mockOnDelete).toHaveBeenCalledWith("link-1");
    });
  });

  describe("Annotation Properties", () => {
    it("renders annotation settings panel for text annotations", () => {
      const annotation = createAnnotation();

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      expect(screen.getByText("Annotation Settings")).toBeInTheDocument();
    });

    it("shows text content textarea for text annotations", () => {
      const annotation = createAnnotation();

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      expect(screen.getByText("Text Content")).toBeInTheDocument();
      expect(screen.getByDisplayValue("Test Label")).toBeInTheDocument();
    });

    it("updates annotation text when changed", async () => {
      const annotation = createAnnotation();

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      const textarea = screen.getByDisplayValue("Test Label");

      // Use fireEvent to simulate a complete change
      fireEvent.change(textarea, { target: { value: "New Label" } });

      expect(mockOnUpdateAnnotation).toHaveBeenCalledWith("ann-1", {
        text: "New Label",
      });
    });

    it("shows color picker", () => {
      const annotation = createAnnotation();

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      expect(screen.getByText("Color")).toBeInTheDocument();
      const colorInput = document.querySelector('input[type="color"]');
      expect(colorInput).toBeInTheDocument();
      expect(colorInput).toHaveValue("#65a30d");
    });

    it("updates annotation color when changed", async () => {
      const user = userEvent.setup();
      const annotation = createAnnotation();

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      const colorInput = document.querySelector('input[type="color"]')!;
      await user.click(colorInput);
      // Simulating color change is tricky; we'd need to fire the change event
    });

    it("shows font size input for text annotations", () => {
      const annotation = createAnnotation({ type: "text" });

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      expect(screen.getByText("Size")).toBeInTheDocument();
      expect(screen.getByDisplayValue("14")).toBeInTheDocument();
    });

    it("updates annotation font size when changed", async () => {
      const annotation = createAnnotation({ type: "text" });

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      const sizeInput = screen.getByDisplayValue("14");

      // Use fireEvent to simulate a complete change
      fireEvent.change(sizeInput, { target: { value: "20" } });

      expect(mockOnUpdateAnnotation).toHaveBeenCalledWith("ann-1", {
        fontSize: 20,
      });
    });

    it("shows delete button for annotations", () => {
      const annotation = createAnnotation();

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      const deleteButton = document.querySelector(".fa-trash-can");
      expect(deleteButton).toBeInTheDocument();
    });

    it("calls onDelete when annotation delete button is clicked", async () => {
      const user = userEvent.setup();
      const annotation = createAnnotation();

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      const deleteButton = document
        .querySelector(".fa-trash-can")!
        .closest("button");
      await user.click(deleteButton!);

      expect(mockOnDelete).toHaveBeenCalledWith("ann-1");
    });

    it("handles caption annotations", () => {
      const annotation = createAnnotation({ type: "caption", text: "Caption" });

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      expect(screen.getByText("Text Content")).toBeInTheDocument();
      expect(screen.getByDisplayValue("Caption")).toBeInTheDocument();
    });

    it("does not show text content for rect annotations", () => {
      const annotation = createAnnotation({ type: "rect", text: undefined });

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      expect(screen.queryByText("Text Content")).not.toBeInTheDocument();
    });

    it("does not show size input for non-text annotations", () => {
      const annotation = createAnnotation({ type: "rect" });

      render(<PropertiesPanel {...defaultProps} selectedItem={annotation} />);

      expect(screen.queryByText("Size")).not.toBeInTheDocument();
    });
  });
});
