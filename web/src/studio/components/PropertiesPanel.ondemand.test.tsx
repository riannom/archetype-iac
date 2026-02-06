/**
 * Tests for PropertiesPanel image sync overlay during on-demand sync scenarios.
 *
 * Verifies the existing image sync overlay (lines 364-390 of PropertiesPanel.tsx)
 * renders correctly for syncing, checking, failed, and cleared states.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import PropertiesPanel from "./PropertiesPanel";
import {
  DeviceNode,
  DeviceType,
  DeviceModel,
  Link,
  Annotation,
} from "../types";
import { RuntimeStatus } from "./RuntimeControl";
import { PortManager } from "../hooks/usePortManager";

// Mock ExternalNetworkConfig component
vi.mock("./ExternalNetworkConfig", () => ({
  default: () => <div data-testid="external-network-config" />,
}));

// Mock InterfaceSelect component
vi.mock("./InterfaceSelect", () => ({
  default: () => <div data-testid="interface-select" />,
}));

// Mock getAgentColor
vi.mock("../../utils/agentColors", () => ({
  getAgentColor: () => "#aabbcc",
}));

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
];

const createDeviceNode = (
  overrides: Partial<DeviceNode> = {}
): DeviceNode => ({
  id: "node-1",
  name: "ceos-2",
  nodeType: "device",
  type: DeviceType.ROUTER,
  model: "ceos",
  version: "4.28.0F",
  x: 100,
  y: 100,
  cpu: 2,
  memory: 2048,
  config: "",
  container_name: "archetype-lab-ceos-2",
  ...overrides,
});

const createMockPortManager = (): PortManager => ({
  getUsedInterfaces: vi.fn().mockReturnValue(new Set()),
  getAvailableInterfaces: vi.fn().mockReturnValue(["eth1", "eth2"]),
  getNextInterface: vi.fn().mockReturnValue("eth1"),
  isInterfaceUsed: vi.fn().mockReturnValue(false),
  getNodeModel: vi.fn().mockReturnValue("ceos"),
});

interface NodeStateEntry {
  id: string;
  lab_id: string;
  node_id: string;
  node_name: string;
  desired_state: "stopped" | "running";
  actual_state: string;
  error_message?: string | null;
  is_ready?: boolean;
  boot_started_at?: string | null;
  image_sync_status?: string | null;
  image_sync_message?: string | null;
  host_id?: string | null;
  host_name?: string | null;
  created_at: string;
  updated_at: string;
}

const createNodeState = (
  overrides: Partial<NodeStateEntry> = {}
): NodeStateEntry => ({
  id: "ns-1",
  lab_id: "lab-1",
  node_id: "node-1",
  node_name: "ceos-2",
  desired_state: "running",
  actual_state: "undeployed",
  is_ready: false,
  image_sync_status: null,
  image_sync_message: null,
  host_id: "agent-1",
  host_name: "Remote Agent",
  created_at: "2024-01-15T10:00:00Z",
  updated_at: "2024-01-15T10:00:00Z",
  ...overrides,
});

describe("PropertiesPanel - On-Demand Image Sync Overlay", () => {
  const mockNode = createDeviceNode();
  const mockPortManager = createMockPortManager();

  const defaultProps = {
    selectedItem: mockNode as DeviceNode | Link | Annotation | null,
    onUpdateNode: vi.fn(),
    onUpdateLink: vi.fn(),
    onUpdateAnnotation: vi.fn(),
    onDelete: vi.fn(),
    nodes: [mockNode] as (DeviceNode)[],
    links: [] as Link[],
    onOpenConsole: vi.fn(),
    runtimeStates: {} as Record<string, RuntimeStatus>,
    onUpdateStatus: vi.fn(),
    deviceModels: mockDeviceModels,
    portManager: mockPortManager,
    onOpenConfigViewer: vi.fn(),
    agents: [],
    nodeStates: {} as Record<string, NodeStateEntry>,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows syncing progress overlay when node image_sync_status is syncing", () => {
    const nodeState = createNodeState({
      image_sync_status: "syncing",
      image_sync_message: "Pushing ceos:4.28.0F to Remote Agent... 50%",
    });

    render(
      <PropertiesPanel
        {...defaultProps}
        nodeStates={{ "node-1": nodeState }}
      />
    );

    // Verify "Pushing Image" text is rendered
    expect(screen.getByText("Pushing Image")).toBeInTheDocument();

    // Verify progress message is rendered
    expect(
      screen.getByText("Pushing ceos:4.28.0F to Remote Agent... 50%")
    ).toBeInTheDocument();
  });

  it("shows checking state when image_sync_status is checking", () => {
    const nodeState = createNodeState({
      image_sync_status: "checking",
      image_sync_message: null,
    });

    render(
      <PropertiesPanel
        {...defaultProps}
        nodeStates={{ "node-1": nodeState }}
      />
    );

    expect(screen.getByText("Checking Image")).toBeInTheDocument();
  });

  it("shows failed state with error message", () => {
    const nodeState = createNodeState({
      image_sync_status: "failed",
      image_sync_message: "Connection refused to remote agent",
    });

    render(
      <PropertiesPanel
        {...defaultProps}
        nodeStates={{ "node-1": nodeState }}
      />
    );

    expect(screen.getByText("Image Sync Failed")).toBeInTheDocument();
    expect(
      screen.getByText("Connection refused to remote agent")
    ).toBeInTheDocument();
  });

  it("does not show sync overlay when image_sync_status is null", () => {
    const nodeState = createNodeState({
      image_sync_status: null,
      image_sync_message: null,
    });

    render(
      <PropertiesPanel
        {...defaultProps}
        nodeStates={{ "node-1": nodeState }}
      />
    );

    // None of the sync UI elements should be rendered
    expect(screen.queryByText("Pushing Image")).not.toBeInTheDocument();
    expect(screen.queryByText("Checking Image")).not.toBeInTheDocument();
    expect(screen.queryByText("Image Sync Failed")).not.toBeInTheDocument();
    expect(screen.queryByText("Image Ready")).not.toBeInTheDocument();
  });

  it("transitions from syncing to normal after sync completes", () => {
    const syncingState = createNodeState({
      image_sync_status: "syncing",
      image_sync_message: "Syncing...",
    });

    const { rerender } = render(
      <PropertiesPanel
        {...defaultProps}
        nodeStates={{ "node-1": syncingState }}
      />
    );

    // Initially shows syncing
    expect(screen.getByText("Pushing Image")).toBeInTheDocument();

    // Rerender with cleared status
    const clearedState = createNodeState({
      image_sync_status: null,
      image_sync_message: null,
    });

    rerender(
      <PropertiesPanel
        {...defaultProps}
        nodeStates={{ "node-1": clearedState }}
      />
    );

    // Sync overlay should be gone
    expect(screen.queryByText("Pushing Image")).not.toBeInTheDocument();
    expect(screen.queryByText("Syncing...")).not.toBeInTheDocument();
  });
});
