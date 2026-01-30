import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExternalNetworkConfig from "./ExternalNetworkConfig";
import { ExternalNetworkNode } from "../types";

// Mock the API module
vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from "../../api";
const mockApiRequest = vi.mocked(apiRequest);

const createMockNode = (overrides: Partial<ExternalNetworkNode> = {}): ExternalNetworkNode => ({
  id: "ext-net-1",
  name: "Test External Network",
  nodeType: "external",
  connectionType: "vlan",
  x: 100,
  y: 100,
  ...overrides,
});

const mockAgents = [
  { id: "agent-1", name: "Host Alpha" },
  { id: "agent-2", name: "Host Beta" },
];

const mockInterfaces = {
  interfaces: [
    { name: "ens192", state: "up", type: "physical", ipv4_addresses: ["192.168.1.10"], is_vlan: false },
    { name: "eth0", state: "up", type: "physical", ipv4_addresses: ["10.0.0.5"], is_vlan: false },
    { name: "ens192.100", state: "up", type: "vlan", ipv4_addresses: [], is_vlan: true },
    { name: "br0", state: "up", type: "bridge", ipv4_addresses: [], is_vlan: false },
    { name: "veth123", state: "up", type: "veth", ipv4_addresses: [], is_vlan: false },
  ],
};

const mockBridges = {
  bridges: [
    { name: "br0", state: "up", interfaces: ["eth0", "veth123"] },
    { name: "br-prod", state: "up", interfaces: ["ens192"] },
  ],
};

describe("ExternalNetworkConfig", () => {
  const mockOnUpdate = vi.fn();
  const mockOnDelete = vi.fn();

  const defaultProps = {
    node: createMockNode(),
    onUpdate: mockOnUpdate,
    onDelete: mockOnDelete,
    agents: mockAgents,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockApiRequest.mockReset();
  });

  describe("Header section", () => {
    it("renders the header with title", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(screen.getByText("External Network")).toBeInTheDocument();
    });

    it("shows VLAN Connection subtitle for VLAN type", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(screen.getByText("VLAN Connection")).toBeInTheDocument();
    });

    it("shows Bridge Connection subtitle for bridge type", () => {
      const bridgeNode = createMockNode({ connectionType: "bridge" });
      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      expect(screen.getByText("Bridge Connection")).toBeInTheDocument();
    });

    it("calls onDelete when delete button is clicked", async () => {
      const user = userEvent.setup();

      render(<ExternalNetworkConfig {...defaultProps} />);

      const deleteButton = document.querySelector(".fa-trash-can")?.closest("button");
      await user.click(deleteButton!);

      expect(mockOnDelete).toHaveBeenCalledWith("ext-net-1");
    });
  });

  describe("Display Name field", () => {
    it("renders display name input with current value", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(screen.getByText("Display Name")).toBeInTheDocument();
      const input = screen.getByDisplayValue("Test External Network");
      expect(input).toBeInTheDocument();
    });

    it("calls onUpdate when display name is changed", async () => {
      const user = userEvent.setup();

      render(<ExternalNetworkConfig {...defaultProps} />);

      const input = screen.getByDisplayValue("Test External Network");
      // Type a single character to test the onChange handler
      await user.type(input, "X");

      // Verify onUpdate was called with the name field appended
      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { name: "Test External NetworkX" });
    });
  });

  describe("Host Agent selection", () => {
    const getHostAgentSelect = () => {
      const label = screen.getByText("Host Agent");
      return label.parentElement?.querySelector("select") as HTMLSelectElement;
    };

    it("renders host agent dropdown with agents", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(screen.getByText("Host Agent")).toBeInTheDocument();
      expect(screen.getByText("Select host...")).toBeInTheDocument();
      expect(screen.getByText("Host Alpha")).toBeInTheDocument();
      expect(screen.getByText("Host Beta")).toBeInTheDocument();
    });

    it("shows currently selected host", () => {
      const nodeWithHost = createMockNode({ host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      const select = getHostAgentSelect();
      expect(select.value).toBe("agent-1");
    });

    it("calls onUpdate when host is changed", async () => {
      const user = userEvent.setup();

      render(<ExternalNetworkConfig {...defaultProps} />);

      const select = getHostAgentSelect();
      await user.selectOptions(select, "agent-1");

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { host: "agent-1" });
    });

    it("clears host when empty option is selected", async () => {
      const user = userEvent.setup();
      const nodeWithHost = createMockNode({ host: "agent-1" });

      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      const select = getHostAgentSelect();
      await user.selectOptions(select, "");

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { host: undefined });
    });
  });

  describe("Connection Type toggle", () => {
    it("renders connection type buttons", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(screen.getByText("Connection Type")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /vlan/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /bridge/i })).toBeInTheDocument();
    });

    it("highlights VLAN button when VLAN is selected", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      const vlanButton = screen.getByRole("button", { name: /vlan/i });
      expect(vlanButton).toHaveClass("border-blue-500");
    });

    it("highlights Bridge button when bridge is selected", () => {
      const bridgeNode = createMockNode({ connectionType: "bridge" });
      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      const bridgeButton = screen.getByRole("button", { name: /bridge/i });
      expect(bridgeButton).toHaveClass("border-purple-500");
    });

    it("calls onUpdate with vlan type when VLAN is clicked", async () => {
      const user = userEvent.setup();
      const bridgeNode = createMockNode({ connectionType: "bridge", bridgeName: "br0" });

      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      await user.click(screen.getByRole("button", { name: /vlan/i }));

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", {
        connectionType: "vlan",
        bridgeName: undefined,
      });
    });

    it("calls onUpdate with bridge type when Bridge is clicked", async () => {
      const user = userEvent.setup();
      const vlanNode = createMockNode({ parentInterface: "ens192", vlanId: 100 });

      render(<ExternalNetworkConfig {...defaultProps} node={vlanNode} />);

      await user.click(screen.getByRole("button", { name: /bridge/i }));

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", {
        connectionType: "bridge",
        parentInterface: undefined,
        vlanId: undefined,
      });
    });
  });

  describe("Loading state", () => {
    it("shows loading indicator while fetching network info", async () => {
      mockApiRequest.mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve(mockInterfaces), 100))
      );

      const nodeWithHost = createMockNode({ host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      expect(screen.getByText("Loading network info...")).toBeInTheDocument();

      await waitFor(() => {
        expect(screen.queryByText("Loading network info...")).not.toBeInTheDocument();
      });
    });
  });

  describe("Error state", () => {
    it("shows error message when loading network info fails", async () => {
      // Suppress console.error for this test since we expect an error
      const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

      mockApiRequest.mockImplementation(() => {
        throw new Error("Network error");
      });

      const nodeWithHost = createMockNode({ host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      await waitFor(() => {
        expect(screen.getByText(/failed to load network/i)).toBeInTheDocument();
      });

      consoleSpy.mockRestore();
    });
  });

  describe("Network info fetching", () => {
    it("fetches interfaces and bridges when host is selected", async () => {
      mockApiRequest
        .mockResolvedValueOnce(mockInterfaces)
        .mockResolvedValueOnce(mockBridges);

      const nodeWithHost = createMockNode({ host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith("/agents/agent-1/interfaces");
        expect(mockApiRequest).toHaveBeenCalledWith("/agents/agent-1/bridges");
      });
    });

    it("does not fetch when no host is selected", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(mockApiRequest).not.toHaveBeenCalled();
    });

    it("clears interfaces and bridges when host is cleared", async () => {
      mockApiRequest
        .mockResolvedValueOnce(mockInterfaces)
        .mockResolvedValueOnce(mockBridges);

      const nodeWithHost = createMockNode({ host: "agent-1" });
      const { rerender } = render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledTimes(2);
      });

      // Simulate host being cleared
      const nodeWithoutHost = createMockNode({ host: undefined });
      rerender(<ExternalNetworkConfig {...defaultProps} node={nodeWithoutHost} />);

      // Should not have made additional calls
      expect(mockApiRequest).toHaveBeenCalledTimes(2);
    });
  });

  describe("VLAN Configuration", () => {
    const getParentInterfaceSelect = () => {
      const label = screen.getByText("Parent Interface");
      return label.parentElement?.querySelector("select") as HTMLSelectElement;
    };

    beforeEach(() => {
      mockApiRequest
        .mockResolvedValueOnce(mockInterfaces)
        .mockResolvedValueOnce(mockBridges);
    });

    it("shows VLAN config section when VLAN is selected", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(screen.getByText("Parent Interface")).toBeInTheDocument();
      expect(screen.getByText("VLAN ID")).toBeInTheDocument();
    });

    it("does not show VLAN config when bridge is selected", () => {
      const bridgeNode = createMockNode({ connectionType: "bridge" });
      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      expect(screen.queryByText("Parent Interface")).not.toBeInTheDocument();
      expect(screen.queryByText("VLAN ID")).not.toBeInTheDocument();
    });

    it("disables parent interface select when no host is selected", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      const select = getParentInterfaceSelect();
      expect(select).toBeDisabled();
      expect(screen.getByText("Select host first")).toBeInTheDocument();
    });

    it("enables parent interface select when host is selected", async () => {
      const nodeWithHost = createMockNode({ host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      await waitFor(() => {
        const select = getParentInterfaceSelect();
        expect(select).not.toBeDisabled();
      });
    });

    it("filters out VLANs, bridges, and veth interfaces from parent interface list", async () => {
      const nodeWithHost = createMockNode({ host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      await waitFor(() => {
        expect(screen.getByText("ens192 (up)")).toBeInTheDocument();
        expect(screen.getByText("eth0 (up)")).toBeInTheDocument();
        expect(screen.queryByText("ens192.100")).not.toBeInTheDocument(); // VLAN
        expect(screen.queryByText("br0")).not.toBeInTheDocument(); // Bridge
        expect(screen.queryByText("veth123")).not.toBeInTheDocument(); // veth
      });
    });

    it("calls onUpdate when parent interface is changed", async () => {
      const user = userEvent.setup();
      const nodeWithHost = createMockNode({ host: "agent-1" });

      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      await waitFor(() => {
        expect(screen.getByText("ens192 (up)")).toBeInTheDocument();
      });

      const select = getParentInterfaceSelect();
      await user.selectOptions(select, "ens192");

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { parentInterface: "ens192" });
    });

    it("calls onUpdate when VLAN ID is changed", async () => {
      const user = userEvent.setup();

      render(<ExternalNetworkConfig {...defaultProps} />);

      const vlanInput = screen.getByPlaceholderText("100");
      // Type a single digit to test the onChange handler
      await user.type(vlanInput, "5");

      // Verify onUpdate was called with vlanId
      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { vlanId: 5 });
    });

    it("clears VLAN ID when input is empty", async () => {
      const user = userEvent.setup();
      const nodeWithVlan = createMockNode({ vlanId: 100 });

      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithVlan} />);

      const vlanInput = screen.getByDisplayValue("100");
      await user.clear(vlanInput);

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { vlanId: undefined });
    });

    it("shows interface preview when both parent interface and VLAN ID are set", () => {
      const nodeWithVlan = createMockNode({
        parentInterface: "ens192",
        vlanId: 100,
      });

      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithVlan} />);

      expect(screen.getByText("Interface Preview")).toBeInTheDocument();
      expect(screen.getByText("ens192.100")).toBeInTheDocument();
    });

    it("does not show interface preview when parent interface is missing", () => {
      const nodeWithVlan = createMockNode({ vlanId: 100 });

      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithVlan} />);

      expect(screen.queryByText("Interface Preview")).not.toBeInTheDocument();
    });

    it("does not show interface preview when VLAN ID is missing", () => {
      const nodeWithParent = createMockNode({ parentInterface: "ens192" });

      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithParent} />);

      expect(screen.queryByText("Interface Preview")).not.toBeInTheDocument();
    });
  });

  describe("Bridge Configuration", () => {
    const getBridgeNameSelect = () => {
      const label = screen.getByText("Bridge Name");
      return label.parentElement?.querySelector("select") as HTMLSelectElement;
    };

    beforeEach(() => {
      mockApiRequest
        .mockResolvedValueOnce(mockInterfaces)
        .mockResolvedValueOnce(mockBridges);
    });

    it("shows Bridge config section when bridge is selected", () => {
      const bridgeNode = createMockNode({ connectionType: "bridge" });
      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      expect(screen.getByText("Bridge Name")).toBeInTheDocument();
    });

    it("does not show Bridge config when VLAN is selected", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(screen.queryByText("Bridge Name")).not.toBeInTheDocument();
    });

    it("disables bridge select when no host is selected", () => {
      const bridgeNode = createMockNode({ connectionType: "bridge" });
      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      const select = getBridgeNameSelect();
      expect(select).toBeDisabled();
    });

    it("populates bridge dropdown with available bridges", async () => {
      const bridgeNode = createMockNode({ connectionType: "bridge", host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      await waitFor(() => {
        // There should be two bridge options
        const bridgeSelect = getBridgeNameSelect();
        const options = bridgeSelect.querySelectorAll("option");
        // 1 default + 2 bridges = 3 options
        expect(options.length).toBeGreaterThanOrEqual(3);
      });
    });

    it("calls onUpdate when bridge is selected from dropdown", async () => {
      const user = userEvent.setup();
      const bridgeNode = createMockNode({ connectionType: "bridge", host: "agent-1" });

      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalled();
      });

      const select = getBridgeNameSelect();
      await user.selectOptions(select, "br-prod");

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { bridgeName: "br-prod" });
    });

    it("allows manual bridge name entry", async () => {
      const user = userEvent.setup();
      const bridgeNode = createMockNode({ connectionType: "bridge", bridgeName: "" });

      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      const manualInput = screen.getByPlaceholderText("br-prod");
      // Type a single character to verify the onChange handler is called
      await user.type(manualInput, "x");

      // Verify onUpdate was called with bridgeName
      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { bridgeName: "x" });
    });

    it("shows or enter manually text", () => {
      const bridgeNode = createMockNode({ connectionType: "bridge" });
      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      expect(screen.getByText("- or enter manually -")).toBeInTheDocument();
    });
  });

  describe("Info Box", () => {
    it("shows VLAN info when VLAN is selected", () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(
        screen.getByText(/VLAN sub-interfaces are automatically created/i)
      ).toBeInTheDocument();
    });

    it("shows bridge info when bridge is selected", () => {
      const bridgeNode = createMockNode({ connectionType: "bridge" });
      render(<ExternalNetworkConfig {...defaultProps} node={bridgeNode} />);

      expect(
        screen.getByText(/Bridge connections use an existing Linux bridge/i)
      ).toBeInTheDocument();
    });
  });

  describe("Graceful API error handling", () => {
    it("continues with empty interfaces when interfaces API fails", async () => {
      mockApiRequest
        .mockRejectedValueOnce(new Error("Interface error"))
        .mockResolvedValueOnce(mockBridges);

      const nodeWithHost = createMockNode({ host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      // Should not show error since we catch and continue
      await waitFor(() => {
        expect(screen.queryByText("Loading network info...")).not.toBeInTheDocument();
      });
    });

    it("continues with empty bridges when bridges API fails", async () => {
      mockApiRequest
        .mockResolvedValueOnce(mockInterfaces)
        .mockRejectedValueOnce(new Error("Bridge error"));

      const nodeWithHost = createMockNode({ host: "agent-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={nodeWithHost} />);

      await waitFor(() => {
        expect(screen.queryByText("Loading network info...")).not.toBeInTheDocument();
      });
    });
  });

  describe("Empty agents list", () => {
    it("renders without agents", () => {
      render(<ExternalNetworkConfig {...defaultProps} agents={[]} />);

      const label = screen.getByText("Host Agent");
      const select = label.parentElement?.querySelector("select") as HTMLSelectElement;
      const options = select.querySelectorAll("option");
      expect(options.length).toBe(1); // Just "Select host..."
    });

    it("renders with undefined agents (defaults to empty)", () => {
      render(<ExternalNetworkConfig {...defaultProps} agents={undefined} />);

      expect(screen.getByText("Select host...")).toBeInTheDocument();
    });
  });
});
