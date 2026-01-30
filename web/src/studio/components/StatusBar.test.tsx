import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import StatusBar from "./StatusBar";

// Mock config
vi.mock("../../config", () => ({
  APP_VERSION: "1.2.3",
  APP_VERSION_LABEL: "TEST",
}));

// Mock formatUptime
vi.mock("../../utils/format", () => ({
  formatUptime: vi.fn((ms: number) => {
    const totalSeconds = Math.floor(ms / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
  }),
}));

interface NodeStateEntry {
  id: string;
  lab_id: string;
  node_id: string;
  node_name: string;
  desired_state: "stopped" | "running";
  actual_state: "undeployed" | "pending" | "running" | "stopped" | "error";
  error_message?: string | null;
  is_ready?: boolean;
  boot_started_at?: string | null;
  created_at: string;
  updated_at: string;
}

describe("StatusBar", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const createNodeState = (overrides: Partial<NodeStateEntry> = {}): NodeStateEntry => ({
    id: "state-1",
    lab_id: "lab-1",
    node_id: "node-1",
    node_name: "router1",
    desired_state: "running",
    actual_state: "running",
    is_ready: true,
    boot_started_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  });

  describe("rendering", () => {
    it("renders the status bar", () => {
      render(<StatusBar nodeStates={{}} />);
      expect(screen.getByText(/UPTIME:/)).toBeInTheDocument();
    });

    it("displays version information", () => {
      render(<StatusBar nodeStates={{}} />);
      expect(screen.getByText("v1.2.3-TEST")).toBeInTheDocument();
    });

    it("shows default uptime when no running nodes", () => {
      render(<StatusBar nodeStates={{}} />);
      expect(screen.getByText(/--:--:--/)).toBeInTheDocument();
    });
  });

  describe("uptime calculation", () => {
    it("shows default uptime when no nodes have boot_started_at", () => {
      const nodeStates = {
        "node-1": createNodeState({ actual_state: "running", boot_started_at: null }),
      };
      render(<StatusBar nodeStates={nodeStates} />);
      expect(screen.getByText(/--:--:--/)).toBeInTheDocument();
    });

    it("shows default uptime when no running nodes", () => {
      const nodeStates = {
        "node-1": createNodeState({
          actual_state: "stopped",
          boot_started_at: new Date(Date.now() - 3600000).toISOString(),
        }),
      };
      render(<StatusBar nodeStates={nodeStates} />);
      expect(screen.getByText(/--:--:--/)).toBeInTheDocument();
    });

    it("calculates uptime from running node with boot_started_at", () => {
      // Set a fixed current time
      const now = new Date("2024-01-15T12:00:00Z");
      vi.setSystemTime(now);

      const bootTime = new Date("2024-01-15T11:00:00Z"); // 1 hour ago
      const nodeStates = {
        "node-1": createNodeState({
          actual_state: "running",
          boot_started_at: bootTime.toISOString(),
        }),
      };

      render(<StatusBar nodeStates={nodeStates} />);
      expect(screen.getByText(/01:00:00/)).toBeInTheDocument();
    });

    it("uses earliest boot time when multiple nodes are running", () => {
      const now = new Date("2024-01-15T12:00:00Z");
      vi.setSystemTime(now);

      const nodeStates = {
        "node-1": createNodeState({
          id: "state-1",
          node_id: "node-1",
          actual_state: "running",
          boot_started_at: new Date("2024-01-15T10:00:00Z").toISOString(), // 2 hours ago (earliest)
        }),
        "node-2": createNodeState({
          id: "state-2",
          node_id: "node-2",
          actual_state: "running",
          boot_started_at: new Date("2024-01-15T11:00:00Z").toISOString(), // 1 hour ago
        }),
        "node-3": createNodeState({
          id: "state-3",
          node_id: "node-3",
          actual_state: "running",
          boot_started_at: new Date("2024-01-15T11:30:00Z").toISOString(), // 30 min ago
        }),
      };

      render(<StatusBar nodeStates={nodeStates} />);
      expect(screen.getByText(/02:00:00/)).toBeInTheDocument();
    });

    it("ignores stopped nodes when calculating uptime", () => {
      const now = new Date("2024-01-15T12:00:00Z");
      vi.setSystemTime(now);

      const nodeStates = {
        "node-1": createNodeState({
          id: "state-1",
          node_id: "node-1",
          actual_state: "stopped", // Stopped - should be ignored
          boot_started_at: new Date("2024-01-15T08:00:00Z").toISOString(), // 4 hours ago
        }),
        "node-2": createNodeState({
          id: "state-2",
          node_id: "node-2",
          actual_state: "running",
          boot_started_at: new Date("2024-01-15T11:00:00Z").toISOString(), // 1 hour ago
        }),
      };

      render(<StatusBar nodeStates={nodeStates} />);
      expect(screen.getByText(/01:00:00/)).toBeInTheDocument();
    });

    it("sets up interval for uptime updates", () => {
      const now = new Date("2024-01-15T12:00:00Z");
      vi.setSystemTime(now);

      const bootTime = new Date("2024-01-15T11:59:00Z"); // 1 minute ago
      const nodeStates = {
        "node-1": createNodeState({
          actual_state: "running",
          boot_started_at: bootTime.toISOString(),
        }),
      };

      const setIntervalSpy = vi.spyOn(global, "setInterval");

      render(<StatusBar nodeStates={nodeStates} />);

      // Verify that setInterval was called with 1000ms
      expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 1000);

      setIntervalSpy.mockRestore();
    });
  });

  describe("styling", () => {
    it("has correct base styling classes", () => {
      const { container } = render(<StatusBar nodeStates={{}} />);
      const statusBar = container.firstChild as HTMLElement;
      expect(statusBar).toHaveClass("h-8");
      expect(statusBar).toHaveClass("shrink-0");
    });
  });
});
