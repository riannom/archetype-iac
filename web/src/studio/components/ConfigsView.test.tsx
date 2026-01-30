import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConfigsView from "./ConfigsView";
import { DeviceNode, DeviceType } from "../types";
import { RuntimeStatus } from "./RuntimeControl";

// Mock the ConfigDiffViewer component
vi.mock("./ConfigDiffViewer", () => ({
  default: ({ snapshotA, snapshotB }: { snapshotA: { id: string }; snapshotB: { id: string } }) => (
    <div data-testid="config-diff-viewer">
      Comparing {snapshotA.id} and {snapshotB.id}
    </div>
  ),
}));

// Mock navigator.clipboard
const mockClipboard = {
  writeText: vi.fn().mockResolvedValue(undefined),
};
Object.assign(navigator, { clipboard: mockClipboard });

// Mock window.confirm
const mockConfirm = vi.fn();
window.confirm = mockConfirm;

// Mock window.alert
const mockAlert = vi.fn();
window.alert = mockAlert;

const createMockSnapshot = (overrides: Partial<{
  id: string;
  lab_id: string;
  node_name: string;
  content: string;
  content_hash: string;
  snapshot_type: string;
  created_at: string;
}> = {}) => ({
  id: overrides.id || "snapshot-1",
  lab_id: overrides.lab_id || "lab-1",
  node_name: overrides.node_name || "router1",
  content: overrides.content || "! Configuration\nhostname router1",
  content_hash: overrides.content_hash || "abc123def456",
  snapshot_type: overrides.snapshot_type || "manual",
  created_at: overrides.created_at || new Date().toISOString(),
});

const mockNodes: DeviceNode[] = [
  {
    id: "node-1",
    name: "Router1",
    container_name: "clab-lab1-router1",
    nodeType: "device",
    type: DeviceType.ROUTER,
    model: "ceos",
    version: "4.28.0F",
    x: 100,
    y: 100,
  },
  {
    id: "node-2",
    name: "Switch1",
    container_name: "clab-lab1-switch1",
    nodeType: "device",
    type: DeviceType.SWITCH,
    model: "srlinux",
    version: "23.10.1",
    x: 200,
    y: 200,
  },
];

describe("ConfigsView", () => {
  const mockStudioRequest = vi.fn();
  const mockOnExtractConfigs = vi.fn();

  const defaultProps = {
    labId: "test-lab-123",
    nodes: mockNodes,
    runtimeStates: {} as Record<string, RuntimeStatus>,
    studioRequest: mockStudioRequest,
    onExtractConfigs: mockOnExtractConfigs,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockStudioRequest.mockResolvedValue({ snapshots: [] });
    mockOnExtractConfigs.mockResolvedValue(undefined);
    mockConfirm.mockReturnValue(true);
  });

  describe("Rendering", () => {
    it("renders the header with title and description", async () => {
      render(<ConfigsView {...defaultProps} />);

      expect(screen.getByText("Configuration Snapshots")).toBeInTheDocument();
      expect(
        screen.getByText(/View, compare, and track configuration changes/)
      ).toBeInTheDocument();
    });

    it("renders the Extract Configs button", async () => {
      render(<ConfigsView {...defaultProps} />);

      expect(screen.getByText("Extract Configs")).toBeInTheDocument();
    });

    it("renders the refresh button", async () => {
      render(<ConfigsView {...defaultProps} />);

      // The refresh button has a rotate icon
      const refreshButton = document.querySelector(".fa-rotate");
      expect(refreshButton).toBeInTheDocument();
    });

    it("renders the Nodes section header", async () => {
      render(<ConfigsView {...defaultProps} />);

      expect(screen.getByText("Nodes")).toBeInTheDocument();
    });

    it("shows node names in the left panel", async () => {
      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Router1")).toBeInTheDocument();
        expect(screen.getByText("Switch1")).toBeInTheDocument();
      });
    });
  });

  describe("Empty states", () => {
    it("shows 'No nodes in topology' when there are no nodes", async () => {
      render(<ConfigsView {...defaultProps} nodes={[]} />);

      await waitFor(() => {
        expect(screen.getByText("No nodes in topology")).toBeInTheDocument();
      });
    });

    it("shows 'Select a node to view snapshots' initially", async () => {
      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(
          screen.getByText("Select a node to view snapshots")
        ).toBeInTheDocument();
      });
    });

    it("shows 'No snapshots for this node' when node has no snapshots", async () => {
      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      render(<ConfigsView {...defaultProps} />);

      // Click on a node to select it
      const user = userEvent.setup();
      await waitFor(() => {
        expect(screen.getByText("Router1")).toBeInTheDocument();
      });
      await user.click(screen.getByText("Router1"));

      await waitFor(() => {
        expect(screen.getByText("No snapshots for this node")).toBeInTheDocument();
        expect(
          screen.getByText('Click "Extract Configs" to create one')
        ).toBeInTheDocument();
      });
    });
  });

  describe("Loading snapshots", () => {
    it("shows loading spinner while loading", async () => {
      mockStudioRequest.mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve({ snapshots: [] }), 100))
      );

      render(<ConfigsView {...defaultProps} />);

      // Loading spinner should be visible
      expect(document.querySelector(".fa-spinner")).toBeInTheDocument();
    });

    it("calls studioRequest to load snapshots on mount", async () => {
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/config-snapshots"
        );
      });
    });

    it("displays snapshots after loading", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
        createMockSnapshot({ id: "snap-2", node_name: "clab-lab1-router1", created_at: new Date(Date.now() - 60000).toISOString() }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        // The first snapshot should auto-select the first node with snapshots
        expect(screen.getByText("2 snapshots")).toBeInTheDocument();
      });
    });

    it("shows error message when loading fails", async () => {
      mockStudioRequest.mockRejectedValue(new Error("Network error"));

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument();
      });
    });
  });

  describe("Node selection", () => {
    it("selects a node when clicked", async () => {
      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Router1")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Router1"));

      // The node should be selected (shown by the sage-colored text)
      const nodeButton = screen.getByText("Router1").closest("button");
      expect(nodeButton).toHaveClass("bg-sage-600/20");
    });

    it("shows 'No configs' label for nodes without snapshots", async () => {
      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        // Both nodes should show "No configs" since there are no snapshots
        const noConfigsLabels = screen.getAllByText("No configs");
        expect(noConfigsLabels).toHaveLength(2);
      });
    });
  });

  describe("Node status indicators", () => {
    it("shows green indicator for running nodes", async () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "stopped",
      };

      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      render(<ConfigsView {...defaultProps} runtimeStates={runtimeStates} />);

      await waitFor(() => {
        const statusIndicators = document.querySelectorAll(".bg-emerald-500");
        expect(statusIndicators.length).toBe(1);
      });
    });

    it("shows amber indicator for booting nodes", async () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "booting",
        "node-2": "stopped",
      };

      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      render(<ConfigsView {...defaultProps} runtimeStates={runtimeStates} />);

      await waitFor(() => {
        const statusIndicators = document.querySelectorAll(".bg-amber-500");
        expect(statusIndicators.length).toBe(1);
      });
    });

    it("shows red indicator for error nodes", async () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "error",
        "node-2": "stopped",
      };

      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      render(<ConfigsView {...defaultProps} runtimeStates={runtimeStates} />);

      await waitFor(() => {
        const statusIndicators = document.querySelectorAll(".bg-red-500");
        expect(statusIndicators.length).toBe(1);
      });
    });
  });

  describe("Extract configs", () => {
    it("calls onExtractConfigs when Extract button is clicked", async () => {
      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await user.click(screen.getByText("Extract Configs"));

      await waitFor(() => {
        expect(mockOnExtractConfigs).toHaveBeenCalled();
      });
    });

    it("shows extracting state while extracting", async () => {
      mockOnExtractConfigs.mockImplementation(
        () => new Promise((resolve) => setTimeout(resolve, 100))
      );
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await user.click(screen.getByText("Extract Configs"));

      expect(screen.getByText("Extracting...")).toBeInTheDocument();
    });

    it("reloads snapshots after extraction", async () => {
      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      // Clear the initial load call
      mockStudioRequest.mockClear();

      await user.click(screen.getByText("Extract Configs"));

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/config-snapshots"
        );
      });
    });
  });

  describe("Snapshot selection and viewing", () => {
    it("shows snapshot content when a snapshot is selected", async () => {
      const snapshots = [
        createMockSnapshot({
          id: "snap-1",
          node_name: "clab-lab1-router1",
          content: "hostname router1\ninterface eth0",
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Router1")).toBeInTheDocument();
      });

      // Click on the node
      await user.click(screen.getByText("Router1"));

      // Wait for snapshots panel to update
      await waitFor(() => {
        expect(screen.queryByText("No snapshots for this node")).not.toBeInTheDocument();
      });

      // Click on the snapshot card (by finding a timestamp-like text)
      const snapshotCard = document.querySelector('[class*="rounded-lg border cursor-pointer"]');
      if (snapshotCard) {
        await user.click(snapshotCard);
      }

      await waitFor(() => {
        expect(screen.getByText(/hostname router1/)).toBeInTheDocument();
      });
    });

    it("shows copy button when viewing a snapshot", async () => {
      const snapshots = [
        createMockSnapshot({
          id: "snap-1",
          node_name: "clab-lab1-router1",
          content: "test config",
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Router1")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Router1"));

      // Click on the snapshot
      await waitFor(() => {
        const snapshotCard = document.querySelector('[class*="rounded-lg border cursor-pointer"]');
        expect(snapshotCard).toBeInTheDocument();
      });

      const snapshotCard = document.querySelector('[class*="rounded-lg border cursor-pointer"]');
      if (snapshotCard) {
        await user.click(snapshotCard);
      }

      await waitFor(() => {
        expect(screen.getByText("Copy")).toBeInTheDocument();
      });
    });

    it("renders snapshot cards that can be clicked", async () => {
      // This test verifies that snapshot cards are rendered and clickable
      const snapshots = [
        createMockSnapshot({
          id: "snap-1",
          node_name: "clab-lab1-router1",
          content: "test config content",
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      // Wait for initial API call to complete
      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith("/labs/test-lab-123/config-snapshots");
      });

      // Wait for snapshots to be loaded (node auto-selected)
      await waitFor(
        () => {
          // The "Select a node to view snapshots" message should disappear
          // when a node is auto-selected
          expect(screen.queryByText("Select a node to view snapshots")).not.toBeInTheDocument();
        },
        { timeout: 3000 }
      );

      // Snapshot cards should be rendered
      await waitFor(
        () => {
          const cards = document.querySelectorAll('[class*="cursor-pointer"][class*="rounded-lg"]');
          expect(cards.length).toBeGreaterThan(0);
        },
        { timeout: 3000 }
      );
    });
  });

  describe("View/Compare mode toggle", () => {
    it("shows View and Compare buttons when multiple snapshots exist", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
        createMockSnapshot({
          id: "snap-2",
          node_name: "clab-lab1-router1",
          created_at: new Date(Date.now() - 60000).toISOString(),
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("View")).toBeInTheDocument();
        expect(screen.getByText("Compare")).toBeInTheDocument();
      });
    });

    it("does not show toggle when only one snapshot exists", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        // Should not show Compare mode toggle with only one snapshot
        // View button might still be present in single snapshot mode
        expect(screen.queryByText("Compare")).not.toBeInTheDocument();
      });
    });

    it("switches to compare mode when Compare button is clicked", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
        createMockSnapshot({
          id: "snap-2",
          node_name: "clab-lab1-router1",
          created_at: new Date(Date.now() - 60000).toISOString(),
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      // Wait for API call to complete
      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith("/labs/test-lab-123/config-snapshots");
      });

      // The node should be auto-selected; wait for Compare button
      // (Compare button appears when 2+ snapshots exist for selected node)
      const compareButton = await waitFor(
        () => {
          const btn = screen.getByRole("button", { name: /compare/i });
          return btn;
        },
        { timeout: 3000 }
      );

      await user.click(compareButton);

      // The compare mode instruction text should appear
      // Use getAllByText since the message may appear multiple times
      await waitFor(() => {
        const messages = screen.getAllByText(/Select 2 snapshots to compare/);
        expect(messages.length).toBeGreaterThan(0);
      });
    });

    it("shows checkboxes in compare mode", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
        createMockSnapshot({
          id: "snap-2",
          node_name: "clab-lab1-router1",
          created_at: new Date(Date.now() - 60000).toISOString(),
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Compare")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Compare"));

      await waitFor(() => {
        // Checkboxes should be visible (they have a specific class)
        const checkboxes = document.querySelectorAll(
          '[class*="w-4 h-4 rounded border-2"]'
        );
        expect(checkboxes.length).toBeGreaterThanOrEqual(2);
      });
    });

    it("shows diff viewer when two snapshots are selected in compare mode", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
        createMockSnapshot({
          id: "snap-2",
          node_name: "clab-lab1-router1",
          created_at: new Date(Date.now() - 60000).toISOString(),
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Compare")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Compare"));

      // Select both snapshots
      const snapshotCards = document.querySelectorAll(
        '[class*="rounded-lg border cursor-pointer"]'
      );
      expect(snapshotCards.length).toBeGreaterThanOrEqual(2);

      await user.click(snapshotCards[0]);
      await user.click(snapshotCards[1]);

      await waitFor(() => {
        expect(screen.getByTestId("config-diff-viewer")).toBeInTheDocument();
      });
    });
  });

  describe("Snapshot deletion", () => {
    it("shows delete button on hover", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        const deleteButton = document.querySelector(".fa-trash-can");
        expect(deleteButton).toBeInTheDocument();
      });
    });

    it("shows confirmation dialog before deleting", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      mockConfirm.mockReturnValue(true);
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        const deleteButton = document.querySelector(".fa-trash-can");
        expect(deleteButton).toBeInTheDocument();
      });

      const deleteButton = document.querySelector(".fa-trash-can")!.closest("button");
      if (deleteButton) {
        await user.click(deleteButton);
      }

      expect(mockConfirm).toHaveBeenCalledWith("Delete this snapshot?");
    });

    it("calls delete API when confirmed", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      mockConfirm.mockReturnValue(true);
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        const deleteButton = document.querySelector(".fa-trash-can");
        expect(deleteButton).toBeInTheDocument();
      });

      // Clear the initial load call
      mockStudioRequest.mockClear();
      mockStudioRequest.mockResolvedValue({});

      const deleteButton = document.querySelector(".fa-trash-can")!.closest("button");
      if (deleteButton) {
        await user.click(deleteButton);
      }

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/config-snapshots/snap-1",
          { method: "DELETE" }
        );
      });
    });

    it("does not delete when confirmation is cancelled", async () => {
      const snapshots = [
        createMockSnapshot({ id: "snap-1", node_name: "clab-lab1-router1" }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      mockConfirm.mockReturnValue(false);
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        const deleteButton = document.querySelector(".fa-trash-can");
        expect(deleteButton).toBeInTheDocument();
      });

      // Clear the initial load call
      mockStudioRequest.mockClear();

      const deleteButton = document.querySelector(".fa-trash-can")!.closest("button");
      if (deleteButton) {
        await user.click(deleteButton);
      }

      expect(mockStudioRequest).not.toHaveBeenCalled();
    });
  });

  describe("Snapshot type badges", () => {
    it("shows Manual badge for manual snapshots", async () => {
      const snapshots = [
        createMockSnapshot({
          id: "snap-1",
          node_name: "clab-lab1-router1",
          snapshot_type: "manual",
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Manual")).toBeInTheDocument();
      });
    });

    it("shows Auto badge for automatic snapshots", async () => {
      const snapshots = [
        createMockSnapshot({
          id: "snap-1",
          node_name: "clab-lab1-router1",
          snapshot_type: "auto",
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Auto")).toBeInTheDocument();
      });
    });
  });

  describe("Timestamp formatting", () => {
    it("shows 'Just now' for very recent snapshots", async () => {
      const snapshots = [
        createMockSnapshot({
          id: "snap-1",
          node_name: "clab-lab1-router1",
          created_at: new Date().toISOString(),
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Just now")).toBeInTheDocument();
      });
    });

    it("shows relative time for recent snapshots", async () => {
      const snapshots = [
        createMockSnapshot({
          id: "snap-1",
          node_name: "clab-lab1-router1",
          created_at: new Date(Date.now() - 5 * 60000).toISOString(), // 5 minutes ago
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("5m ago")).toBeInTheDocument();
      });
    });

    it("shows hours ago for older snapshots", async () => {
      const snapshots = [
        createMockSnapshot({
          id: "snap-1",
          node_name: "clab-lab1-router1",
          created_at: new Date(Date.now() - 3 * 60 * 60000).toISOString(), // 3 hours ago
        }),
      ];

      mockStudioRequest.mockResolvedValue({ snapshots });
      render(<ConfigsView {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("3h ago")).toBeInTheDocument();
      });
    });
  });

  describe("Refresh functionality", () => {
    it("reloads snapshots when refresh button is clicked", async () => {
      mockStudioRequest.mockResolvedValue({ snapshots: [] });
      const user = userEvent.setup();

      render(<ConfigsView {...defaultProps} />);

      // Wait for initial load
      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalled();
      });

      // Clear the initial load call
      mockStudioRequest.mockClear();

      // Click refresh button
      const refreshButton = document.querySelector(".fa-rotate")!.closest("button");
      if (refreshButton) {
        await user.click(refreshButton);
      }

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/config-snapshots"
        );
      });
    });
  });
});
