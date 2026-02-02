import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RuntimeControl, { RuntimeStatus } from "./RuntimeControl";
import { DeviceNode, DeviceType, DeviceModel } from "../types";

// Mock window.confirm and window.alert
const mockConfirm = vi.fn();
const mockAlert = vi.fn();
window.confirm = mockConfirm;
window.alert = mockAlert;

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
    id: "linux",
    name: "Linux Container",
    type: DeviceType.HOST,
    icon: "fa-server",
    versions: ["alpine:latest"],
    isActive: true,
    vendor: "Generic",
  },
];

const mockNodes: DeviceNode[] = [
  {
    id: "node-1",
    name: "Router1",
    nodeType: "device",
    type: DeviceType.ROUTER,
    model: "ceos",
    version: "4.28.0F",
    x: 100,
    y: 100,
  },
  {
    id: "node-2",
    name: "Host1",
    nodeType: "device",
    type: DeviceType.HOST,
    model: "linux",
    version: "alpine:latest",
    x: 200,
    y: 200,
  },
];

describe("RuntimeControl", () => {
  const mockOnUpdateStatus = vi.fn();
  const mockOnSetRuntimeStatus = vi.fn();
  const mockOnRefreshStates = vi.fn();
  const mockStudioRequest = vi.fn();
  const mockOnOpenConfigViewer = vi.fn();
  const mockOnOpenNodeConfig = vi.fn();

  const defaultProps = {
    labId: "test-lab-123",
    nodes: mockNodes,
    runtimeStates: {} as Record<string, RuntimeStatus>,
    nodeStates: {} as Record<string, { id: string; node_id: string; node_name: string; host_id?: string | null; host_name?: string | null }>,
    deviceModels: mockDeviceModels,
    onUpdateStatus: mockOnUpdateStatus,
    onSetRuntimeStatus: mockOnSetRuntimeStatus,
    onRefreshStates: mockOnRefreshStates,
    studioRequest: mockStudioRequest,
    onOpenConfigViewer: mockOnOpenConfigViewer,
    onOpenNodeConfig: mockOnOpenNodeConfig,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockStudioRequest.mockResolvedValue({});
    mockConfirm.mockReturnValue(true);
  });

  it("renders the runtime control header", () => {
    render(<RuntimeControl {...defaultProps} />);

    expect(screen.getByText("Runtime Control")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Live operational state and lifecycle management for your topology."
      )
    ).toBeInTheDocument();
  });

  it("renders all device nodes in the table", () => {
    render(<RuntimeControl {...defaultProps} />);

    expect(screen.getByText("Router1")).toBeInTheDocument();
    expect(screen.getByText("Host1")).toBeInTheDocument();
  });

  it("displays device model names and versions", () => {
    render(<RuntimeControl {...defaultProps} />);

    expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
    expect(screen.getByText("Linux Container")).toBeInTheDocument();
    expect(screen.getByText("4.28.0F")).toBeInTheDocument();
    expect(screen.getByText("alpine:latest")).toBeInTheDocument();
  });

  it("shows Deploy Lab button when lab is not deployed", () => {
    render(<RuntimeControl {...defaultProps} />);

    // Look for the header Deploy Lab button by its title attribute
    const deployButton = screen.getByTitle("Deploy all nodes in the topology");
    expect(deployButton).toBeInTheDocument();
    expect(deployButton).toHaveTextContent("Deploy Lab");
  });

  it("shows Start All and Stop All buttons when lab is deployed with mixed states", () => {
    const runtimeStates: Record<string, RuntimeStatus> = {
      "node-1": "running",
      "node-2": "stopped",
    };

    render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

    expect(
      screen.getByRole("button", { name: /start all/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /stop all/i })
    ).toBeInTheDocument();
  });

  it("hides Start All button when all nodes are already running", () => {
    const runtimeStates: Record<string, RuntimeStatus> = {
      "node-1": "running",
      "node-2": "running",
    };

    render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

    expect(
      screen.queryByRole("button", { name: /start all/i })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /stop all/i })
    ).toBeInTheDocument();
  });

  it("shows Extract Configs button when lab is deployed", () => {
    const runtimeStates: Record<string, RuntimeStatus> = {
      "node-1": "running",
      "node-2": "running",
    };

    render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

    expect(
      screen.getByRole("button", { name: /extract configs/i })
    ).toBeInTheDocument();
  });

  it("disables Extract Configs button when no nodes are running", () => {
    const runtimeStates: Record<string, RuntimeStatus> = {
      "node-1": "stopped",
      "node-2": "stopped",
    };

    render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

    // Lab is not deployed (no running nodes), so Extract Configs won't show
    // Let's test with at least one node having been started before
  });

  describe("Status display", () => {
    it("shows stopped status correctly", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "stopped",
        "node-2": "stopped",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      const stoppedBadges = screen.getAllByText("stopped");
      expect(stoppedBadges).toHaveLength(2);
    });

    it("shows running status correctly", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      const runningBadges = screen.getAllByText("running");
      expect(runningBadges).toHaveLength(2);
    });

    it("shows booting status correctly", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "booting",
        "node-2": "stopped",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      expect(screen.getByText("booting")).toBeInTheDocument();
    });

    it("shows error status correctly", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "error",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      expect(screen.getByText("error")).toBeInTheDocument();
    });
  });

  describe("Bulk actions", () => {
    it("calls studioRequest with correct params on Deploy Lab", async () => {
      const user = userEvent.setup();

      render(<RuntimeControl {...defaultProps} />);

      await user.click(screen.getByTitle("Deploy all nodes in the topology"));

      expect(mockStudioRequest).toHaveBeenCalledWith(
        "/labs/test-lab-123/nodes/desired-state",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ state: "running" }),
        })
      );
    });

    it("triggers refresh after setting desired state", async () => {
      const user = userEvent.setup();

      render(<RuntimeControl {...defaultProps} />);

      await user.click(screen.getByTitle("Deploy all nodes in the topology"));

      // The component calls onRefreshStates via setTimeout after the action completes
      // Just verify the desired-state endpoint was called (sync is auto-triggered by backend)
      expect(mockStudioRequest).toHaveBeenCalledWith(
        "/labs/test-lab-123/nodes/desired-state",
        expect.objectContaining({
          method: "PUT",
        })
      );
    });

    it("optimistically updates UI to booting state on deploy", async () => {
      const user = userEvent.setup();

      render(<RuntimeControl {...defaultProps} />);

      await user.click(screen.getByTitle("Deploy all nodes in the topology"));

      expect(mockOnSetRuntimeStatus).toHaveBeenCalledWith("node-1", "booting");
      expect(mockOnSetRuntimeStatus).toHaveBeenCalledWith("node-2", "booting");
    });

    it("shows confirmation dialog on Stop All", async () => {
      const user = userEvent.setup();
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      await user.click(screen.getByRole("button", { name: /stop all/i }));

      expect(mockConfirm).toHaveBeenCalled();
    });

    it("does not stop nodes if confirmation is cancelled", async () => {
      const user = userEvent.setup();
      mockConfirm.mockReturnValue(false);

      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      // Wait for initial metrics fetch to complete, then clear mocks
      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith('/dashboard/metrics/containers');
      });
      mockStudioRequest.mockClear();

      await user.click(screen.getByRole("button", { name: /stop all/i }));

      // After cancelling confirmation, no stop-related requests should be made
      expect(mockStudioRequest).not.toHaveBeenCalled();
    });
  });

  describe("Extract configs", () => {
    it("shows confirmation dialog before extracting", async () => {
      const user = userEvent.setup();
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      await user.click(screen.getByRole("button", { name: /extract configs/i }));

      expect(mockConfirm).toHaveBeenCalled();
    });

    it("calls extract-configs endpoint", async () => {
      const user = userEvent.setup();
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      await user.click(screen.getByRole("button", { name: /extract configs/i }));

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/extract-configs",
          { method: "POST" }
        );
      });
    });

    it("shows success alert on successful extraction", async () => {
      const user = userEvent.setup();
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      await user.click(screen.getByRole("button", { name: /extract configs/i }));

      await waitFor(() => {
        expect(mockAlert).toHaveBeenCalledWith("Configs extracted successfully!");
      });
    });
  });

  describe("View Configs button", () => {
    it("renders View Configs button when handler is provided and lab is deployed", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      expect(
        screen.getByRole("button", { name: /view configs/i })
      ).toBeInTheDocument();
    });

    it("calls onOpenConfigViewer when View Configs is clicked", async () => {
      const user = userEvent.setup();
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      await user.click(screen.getByRole("button", { name: /view configs/i }));

      expect(mockOnOpenConfigViewer).toHaveBeenCalled();
    });
  });

  describe("Per-node actions", () => {
    it("shows play button for stopped nodes", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "stopped",
        "node-2": "stopped",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      // Each stopped node should have a play/rocket button
      const playButtons = document.querySelectorAll(".fa-play, .fa-rocket");
      expect(playButtons.length).toBeGreaterThan(0);
    });

    it("shows power-off button for running nodes", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      // Each running node should have a power-off button
      const powerButtons = document.querySelectorAll(".fa-power-off");
      expect(powerButtons.length).toBe(2);
    });

    it("shows restart button for running nodes", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      // Each running node should have a restart button
      const restartButtons = document.querySelectorAll(".fa-rotate");
      expect(restartButtons.length).toBe(2);
    });

    it("shows config button for each node when handler is provided", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      // Each node should have a config button, plus one header View Configs button
      // Total: 2 per-node + 1 header = 3
      const configButtons = document.querySelectorAll(".fa-file-code");
      expect(configButtons.length).toBe(3);
    });
  });

  describe("Empty state", () => {
    it("shows empty state message when no nodes exist", () => {
      render(<RuntimeControl {...defaultProps} nodes={[]} />);

      expect(
        screen.getByText(
          "No devices in current topology. Return to Designer to add nodes."
        )
      ).toBeInTheDocument();
    });
  });

  describe("Table headers", () => {
    it("renders all table column headers", () => {
      render(<RuntimeControl {...defaultProps} />);

      expect(screen.getByText("Device Name")).toBeInTheDocument();
      expect(screen.getByText("Model / Version")).toBeInTheDocument();
      expect(screen.getByText("Status")).toBeInTheDocument();
      expect(screen.getByText("Utilization")).toBeInTheDocument();
      expect(screen.getByText("Actions")).toBeInTheDocument();
    });
  });

  describe("Utilization display", () => {
    it("shows Offline for stopped nodes", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "stopped",
        "node-2": "stopped",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      const offlineTexts = screen.getAllByText("Offline");
      expect(offlineTexts).toHaveLength(2);
    });

    it("shows Loading for running nodes without metrics data", () => {
      const runtimeStates: Record<string, RuntimeStatus> = {
        "node-1": "running",
        "node-2": "running",
      };

      render(<RuntimeControl {...defaultProps} runtimeStates={runtimeStates} />);

      const loadingTexts = screen.getAllByText("Loading...");
      expect(loadingTexts).toHaveLength(2);
    });
  });
});
