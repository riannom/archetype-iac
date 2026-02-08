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
  x: 100,
  y: 100,
  ...overrides,
});

const mockManagedInterfaces = {
  interfaces: [
    {
      id: "mi-1",
      host_id: "agent-1",
      host_name: "Host Alpha",
      name: "eth0.200",
      interface_type: "external",
      parent_interface: "eth0",
      vlan_id: 200,
      ip_address: null,
      desired_mtu: 9000,
      current_mtu: 9000,
      is_up: true,
      sync_status: "synced",
      sync_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "mi-2",
      host_id: "agent-1",
      host_name: "Host Alpha",
      name: "eth0.300",
      interface_type: "external",
      parent_interface: "eth0",
      vlan_id: 300,
      ip_address: "10.0.3.1/24",
      desired_mtu: 1500,
      current_mtu: 1500,
      is_up: true,
      sync_status: "synced",
      sync_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "mi-3",
      host_id: "agent-2",
      host_name: "Host Beta",
      name: "ens192.100",
      interface_type: "external",
      parent_interface: "ens192",
      vlan_id: 100,
      ip_address: null,
      desired_mtu: 9000,
      current_mtu: null,
      is_up: false,
      sync_status: "unconfigured",
      sync_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
};

describe("ExternalNetworkConfig", () => {
  const mockOnUpdate = vi.fn();
  const mockOnDelete = vi.fn();

  const defaultProps = {
    node: createMockNode(),
    onUpdate: mockOnUpdate,
    onDelete: mockOnDelete,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockApiRequest.mockReset();
    mockApiRequest.mockResolvedValue(mockManagedInterfaces);
  });

  describe("Header section", () => {
    it("renders the header with title", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText("External Network")).toBeInTheDocument();
      });
    });

    it("shows Unconfigured subtitle when no interface selected", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText("Unconfigured")).toBeInTheDocument();
      });
    });

    it("shows interface name in subtitle when interface is selected", async () => {
      const node = createMockNode({ managedInterfaceId: "mi-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={node} />);

      await waitFor(() => {
        // The header subtitle shows the selected interface name
        const subtitles = document.querySelectorAll(".text-purple-600, .dark\\:text-purple-400");
        const found = Array.from(subtitles).some((el) => el.textContent === "eth0.200");
        expect(found).toBe(true);
      });
    });

    it("calls onDelete when delete button is clicked", async () => {
      const user = userEvent.setup();
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("External Network")).toBeInTheDocument();
      });

      const deleteButton = document.querySelector(".fa-trash-can")?.closest("button");
      await user.click(deleteButton!);

      expect(mockOnDelete).toHaveBeenCalledWith("ext-net-1");
    });
  });

  describe("Display Name field", () => {
    it("renders display name input with current value", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Display Name")).toBeInTheDocument();
      });
      const input = screen.getByDisplayValue("Test External Network");
      expect(input).toBeInTheDocument();
    });

    it("calls onUpdate when display name is changed", async () => {
      const user = userEvent.setup();
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByDisplayValue("Test External Network")).toBeInTheDocument();
      });

      const input = screen.getByDisplayValue("Test External Network");
      await user.type(input, "X");

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", { name: "Test External NetworkX" });
    });
  });

  describe("Legacy Warning", () => {
    it("shows legacy warning for nodes with old-style fields", async () => {
      const legacyNode = createMockNode({
        connectionType: "vlan",
        parentInterface: "eth0",
        vlanId: 100,
      });
      render(<ExternalNetworkConfig {...defaultProps} node={legacyNode} />);

      await waitFor(() => {
        expect(screen.getByText("Legacy Configuration")).toBeInTheDocument();
      });
    });

    it("shows current legacy config details", async () => {
      const legacyNode = createMockNode({
        connectionType: "vlan",
        parentInterface: "ens192",
        vlanId: 200,
      });
      render(<ExternalNetworkConfig {...defaultProps} node={legacyNode} />);

      await waitFor(() => {
        expect(screen.getByText("Current: ens192.200")).toBeInTheDocument();
      });
    });

    it("does not show legacy warning for nodes with managed interface", async () => {
      const node = createMockNode({ managedInterfaceId: "mi-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={node} />);

      await waitFor(() => {
        expect(screen.queryByText("Legacy Configuration")).not.toBeInTheDocument();
      });
    });

    it("does not show legacy warning for new unconfigured nodes", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.queryByText("Legacy Configuration")).not.toBeInTheDocument();
      });
    });
  });

  describe("Infrastructure Interface Selection", () => {
    it("fetches external interfaces on mount", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          "/infrastructure/interfaces?interface_type=external"
        );
      });
    });

    it("shows loading state while fetching", async () => {
      mockApiRequest.mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve(mockManagedInterfaces), 100))
      );

      render(<ExternalNetworkConfig {...defaultProps} />);

      expect(screen.getByText("Loading interfaces...")).toBeInTheDocument();

      await waitFor(() => {
        expect(screen.queryByText("Loading interfaces...")).not.toBeInTheDocument();
      });
    });

    it("shows error state when API call fails", async () => {
      const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      mockApiRequest.mockRejectedValue(new Error("API Error"));

      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Failed to load infrastructure interfaces")).toBeInTheDocument();
      });

      consoleSpy.mockRestore();
    });

    it("shows empty state when no interfaces exist", async () => {
      mockApiRequest.mockResolvedValue({ interfaces: [] });

      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/No external interfaces configured/)).toBeInTheDocument();
      });
    });

    it("renders interface dropdown grouped by host", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Select interface...")).toBeInTheDocument();
      });

      // Check optgroup labels
      const optgroups = document.querySelectorAll("optgroup");
      expect(optgroups.length).toBe(2); // Host Alpha and Host Beta
    });

    it("shows interface names with VLAN IDs", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        const options = document.querySelectorAll("option");
        const optionTexts = Array.from(options).map((o) => o.textContent);
        expect(optionTexts).toContain("eth0.200 (VLAN 200) \u2713");
        expect(optionTexts).toContain("eth0.300 (VLAN 300) \u2713");
        expect(optionTexts).toContain("ens192.100 (VLAN 100)");
      });
    });

    it("calls onUpdate when interface is selected", async () => {
      const user = userEvent.setup();
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Select interface...")).toBeInTheDocument();
      });

      const select = screen.getByText("Select interface...").closest("select")!;
      await user.selectOptions(select, "mi-1");

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", {
        managedInterfaceId: "mi-1",
        managedInterfaceName: "eth0.200",
        managedInterfaceHostId: "agent-1",
        managedInterfaceHostName: "Host Alpha",
        host: "agent-1",
        connectionType: undefined,
        parentInterface: undefined,
        vlanId: undefined,
        bridgeName: undefined,
      });
    });

    it("clears selection when empty option is chosen", async () => {
      const user = userEvent.setup();
      const node = createMockNode({ managedInterfaceId: "mi-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={node} />);

      await waitFor(() => {
        expect(screen.getByText("Select interface...")).toBeInTheDocument();
      });

      const select = screen.getByText("Select interface...").closest("select")!;
      await user.selectOptions(select, "");

      expect(mockOnUpdate).toHaveBeenCalledWith("ext-net-1", {
        managedInterfaceId: undefined,
        managedInterfaceName: undefined,
        managedInterfaceHostId: undefined,
        managedInterfaceHostName: undefined,
        host: undefined,
      });
    });
  });

  describe("Interface Details", () => {
    it("shows interface details when interface is selected", async () => {
      const node = createMockNode({ managedInterfaceId: "mi-1" });
      render(<ExternalNetworkConfig {...defaultProps} node={node} />);

      await waitFor(() => {
        expect(screen.getByText("Interface Details")).toBeInTheDocument();
        // "eth0.200" appears in both header subtitle and details panel
        expect(screen.getAllByText("eth0.200").length).toBeGreaterThanOrEqual(2);
        expect(screen.getByText("Host Alpha")).toBeInTheDocument();
        expect(screen.getByText("eth0")).toBeInTheDocument(); // parent
        expect(screen.getByText("200")).toBeInTheDocument(); // VLAN
        expect(screen.getByText("9000")).toBeInTheDocument(); // MTU
        expect(screen.getByText("synced")).toBeInTheDocument();
      });
    });

    it("shows IP address when available", async () => {
      const node = createMockNode({ managedInterfaceId: "mi-2" });
      render(<ExternalNetworkConfig {...defaultProps} node={node} />);

      await waitFor(() => {
        expect(screen.getByText("10.0.3.1/24")).toBeInTheDocument();
      });
    });

    it("does not show details when no interface is selected", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(screen.queryByText("Interface Details")).not.toBeInTheDocument();
      });
    });
  });

  describe("Info Box", () => {
    it("shows informational text about external networks", async () => {
      render(<ExternalNetworkConfig {...defaultProps} />);

      await waitFor(() => {
        expect(
          screen.getByText(/External networks connect lab devices to physical networks/i)
        ).toBeInTheDocument();
      });
    });
  });
});
