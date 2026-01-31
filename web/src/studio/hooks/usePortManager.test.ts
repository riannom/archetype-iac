import { describe, it, expect, beforeEach, beforeAll } from "vitest";
import { renderHook } from "@testing-library/react";
import { usePortManager } from "./usePortManager";
import { Node, Link, DeviceNode, ExternalNetworkNode, DeviceType, DeviceModel } from "../types";
import { initializePatterns } from "../utils/interfaceRegistry";

// Initialize interface patterns before tests run
// This mimics what DeviceCatalogContext does when loading from /vendors API
const testDeviceModels: DeviceModel[] = [
  {
    id: "linux",
    type: DeviceType.HOST,
    name: "Linux",
    icon: "fa-terminal",
    versions: ["latest"],
    isActive: true,
    vendor: "Open Source",
    portNaming: "eth",
    portStartIndex: 1,
    maxPorts: 32,
  },
  {
    id: "ceos",
    type: DeviceType.SWITCH,
    name: "Arista EOS",
    icon: "fa-arrows-left-right-to-line",
    versions: ["latest"],
    isActive: true,
    vendor: "Arista",
    kind: "ceos",
    portNaming: "Ethernet",
    portStartIndex: 1,
    maxPorts: 64,
  },
  {
    id: "srl",
    type: DeviceType.SWITCH,
    name: "Nokia SR Linux",
    icon: "fa-arrows-left-right-to-line",
    versions: ["latest"],
    isActive: true,
    vendor: "Nokia",
    kind: "nokia_srlinux",
    portNaming: "e1-",
    portStartIndex: 1,
    maxPorts: 34,
  },
];

beforeAll(() => {
  initializePatterns(testDeviceModels);
});

// Factory functions for test data
const createDeviceNode = (overrides: Partial<DeviceNode> = {}): DeviceNode => ({
  id: overrides.id || "node-1",
  name: overrides.name || "Router1",
  nodeType: "device",
  type: overrides.type || DeviceType.ROUTER,
  model: overrides.model || "linux",
  version: overrides.version || "latest",
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

describe("usePortManager", () => {
  describe("Initialization", () => {
    it("initializes with empty nodes and links", () => {
      const { result } = renderHook(() => usePortManager([], []));

      expect(result.current).toBeDefined();
      expect(typeof result.current.getUsedInterfaces).toBe("function");
      expect(typeof result.current.getAvailableInterfaces).toBe("function");
      expect(typeof result.current.getNextInterface).toBe("function");
      expect(typeof result.current.isInterfaceUsed).toBe("function");
      expect(typeof result.current.getNodeModel).toBe("function");
    });

    it("tracks device node models", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1", model: "ceos" }),
        createDeviceNode({ id: "node-2", model: "srl" }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, []));

      expect(result.current.getNodeModel("node-1")).toBe("ceos");
      expect(result.current.getNodeModel("node-2")).toBe("srl");
    });

    it("uses 'external' model for external network nodes", () => {
      const nodes: Node[] = [
        createExternalNetworkNode({ id: "ext-1" }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, []));

      expect(result.current.getNodeModel("ext-1")).toBe("external");
    });

    it("returns 'generic' for unknown node IDs", () => {
      const { result } = renderHook(() => usePortManager([], []));

      expect(result.current.getNodeModel("unknown-node")).toBe("generic");
    });
  });

  describe("getUsedInterfaces", () => {
    it("returns empty set for node with no links", () => {
      const nodes: Node[] = [createDeviceNode({ id: "node-1" })];

      const { result } = renderHook(() => usePortManager(nodes, []));

      const usedInterfaces = result.current.getUsedInterfaces("node-1");
      expect(usedInterfaces.size).toBe(0);
    });

    it("tracks source interfaces from links", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1" }),
        createDeviceNode({ id: "node-2" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth2",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      const usedOnNode1 = result.current.getUsedInterfaces("node-1");
      expect(usedOnNode1.has("eth1")).toBe(true);
      expect(usedOnNode1.has("eth2")).toBe(false);
    });

    it("tracks target interfaces from links", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1" }),
        createDeviceNode({ id: "node-2" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth2",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      const usedOnNode2 = result.current.getUsedInterfaces("node-2");
      expect(usedOnNode2.has("eth2")).toBe(true);
      expect(usedOnNode2.has("eth1")).toBe(false);
    });

    it("tracks multiple interfaces per node", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1" }),
        createDeviceNode({ id: "node-2" }),
        createDeviceNode({ id: "node-3" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth1",
        }),
        createLink({
          id: "link-2",
          source: "node-1",
          target: "node-3",
          sourceInterface: "eth2",
          targetInterface: "eth1",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      const usedOnNode1 = result.current.getUsedInterfaces("node-1");
      expect(usedOnNode1.has("eth1")).toBe(true);
      expect(usedOnNode1.has("eth2")).toBe(true);
      expect(usedOnNode1.size).toBe(2);
    });

    it("returns empty set for unknown node", () => {
      const { result } = renderHook(() => usePortManager([], []));

      const usedInterfaces = result.current.getUsedInterfaces("unknown-node");
      expect(usedInterfaces.size).toBe(0);
    });

    it("handles links without interface names", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1" }),
        createDeviceNode({ id: "node-2" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          // No sourceInterface or targetInterface
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      const usedOnNode1 = result.current.getUsedInterfaces("node-1");
      const usedOnNode2 = result.current.getUsedInterfaces("node-2");
      expect(usedOnNode1.size).toBe(0);
      expect(usedOnNode2.size).toBe(0);
    });
  });

  describe("isInterfaceUsed", () => {
    it("returns true for used interface", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1" }),
        createDeviceNode({ id: "node-2" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth2",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      expect(result.current.isInterfaceUsed("node-1", "eth1")).toBe(true);
    });

    it("returns false for unused interface", () => {
      const nodes: Node[] = [createDeviceNode({ id: "node-1" })];

      const { result } = renderHook(() => usePortManager(nodes, []));

      expect(result.current.isInterfaceUsed("node-1", "eth1")).toBe(false);
    });

    it("returns false for unknown node", () => {
      const { result } = renderHook(() => usePortManager([], []));

      expect(result.current.isInterfaceUsed("unknown", "eth1")).toBe(false);
    });
  });

  describe("getAvailableInterfaces", () => {
    it("returns available interfaces for node with no links", () => {
      const nodes: Node[] = [createDeviceNode({ id: "node-1", model: "linux" })];

      const { result } = renderHook(() => usePortManager(nodes, []));

      const available = result.current.getAvailableInterfaces("node-1", 5);
      expect(available.length).toBe(5);
      expect(available[0]).toBe("eth1");
      expect(available[1]).toBe("eth2");
    });

    it("excludes used interfaces from available list", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1", model: "linux" }),
        createDeviceNode({ id: "node-2", model: "linux" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth1",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      const available = result.current.getAvailableInterfaces("node-1", 3);
      expect(available).not.toContain("eth1");
      expect(available[0]).toBe("eth2");
    });

    it("uses correct interface pattern for different device models", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "ceos-node", model: "ceos" }),
        createDeviceNode({ id: "srl-node", model: "srl" }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, []));

      const ceosInterfaces = result.current.getAvailableInterfaces("ceos-node", 2);
      expect(ceosInterfaces[0]).toBe("Ethernet1");

      const srlInterfaces = result.current.getAvailableInterfaces("srl-node", 2);
      expect(srlInterfaces[0]).toBe("e1-1");
    });

    it("returns default count of 10 interfaces", () => {
      const nodes: Node[] = [createDeviceNode({ id: "node-1" })];

      const { result } = renderHook(() => usePortManager(nodes, []));

      const available = result.current.getAvailableInterfaces("node-1");
      expect(available.length).toBe(10);
    });
  });

  describe("getNextInterface", () => {
    it("returns first available interface for node with no links", () => {
      const nodes: Node[] = [createDeviceNode({ id: "node-1", model: "linux" })];

      const { result } = renderHook(() => usePortManager(nodes, []));

      const nextInterface = result.current.getNextInterface("node-1");
      expect(nextInterface).toBe("eth1");
    });

    it("returns next available interface when some are used", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1", model: "linux" }),
        createDeviceNode({ id: "node-2", model: "linux" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth1",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      const nextInterface = result.current.getNextInterface("node-1");
      expect(nextInterface).toBe("eth2");
    });

    it("uses correct pattern for device model", () => {
      const nodes: Node[] = [createDeviceNode({ id: "ceos-node", model: "ceos" })];

      const { result } = renderHook(() => usePortManager(nodes, []));

      const nextInterface = result.current.getNextInterface("ceos-node");
      expect(nextInterface).toBe("Ethernet1");
    });

    it("handles external network nodes", () => {
      const nodes: Node[] = [createExternalNetworkNode({ id: "ext-1" })];

      const { result } = renderHook(() => usePortManager(nodes, []));

      const nextInterface = result.current.getNextInterface("ext-1");
      // External nodes use generic pattern, should get first interface
      expect(nextInterface).toBeDefined();
    });

    it("skips gaps in used interfaces", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1", model: "linux" }),
        createDeviceNode({ id: "node-2", model: "linux" }),
        createDeviceNode({ id: "node-3", model: "linux" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth1",
        }),
        createLink({
          id: "link-2",
          source: "node-1",
          target: "node-3",
          sourceInterface: "eth2",
          targetInterface: "eth1",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      const nextInterface = result.current.getNextInterface("node-1");
      expect(nextInterface).toBe("eth3");
    });
  });

  describe("Reactivity", () => {
    it("updates when nodes change", () => {
      const initialNodes: Node[] = [
        createDeviceNode({ id: "node-1", model: "linux" }),
      ];

      const { result, rerender } = renderHook(
        ({ nodes, links }) => usePortManager(nodes, links),
        {
          initialProps: { nodes: initialNodes, links: [] },
        }
      );

      expect(result.current.getNodeModel("node-1")).toBe("linux");

      const updatedNodes: Node[] = [
        createDeviceNode({ id: "node-1", model: "ceos" }),
      ];

      rerender({ nodes: updatedNodes, links: [] });

      expect(result.current.getNodeModel("node-1")).toBe("ceos");
    });

    it("updates when links change", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1", model: "linux" }),
        createDeviceNode({ id: "node-2", model: "linux" }),
      ];
      const initialLinks: Link[] = [];

      const { result, rerender } = renderHook(
        ({ nodes, links }) => usePortManager(nodes, links),
        {
          initialProps: { nodes, links: initialLinks },
        }
      );

      expect(result.current.isInterfaceUsed("node-1", "eth1")).toBe(false);

      const updatedLinks: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth2",
        }),
      ];

      rerender({ nodes, links: updatedLinks });

      expect(result.current.isInterfaceUsed("node-1", "eth1")).toBe(true);
    });
  });

  describe("Edge Cases", () => {
    it("handles node with many links", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "hub", model: "linux" }),
        ...Array.from({ length: 10 }, (_, i) =>
          createDeviceNode({ id: `spoke-${i}`, model: "linux" })
        ),
      ];

      const links: Link[] = Array.from({ length: 10 }, (_, i) =>
        createLink({
          id: `link-${i}`,
          source: "hub",
          target: `spoke-${i}`,
          sourceInterface: `eth${i + 1}`,
          targetInterface: "eth1",
        })
      );

      const { result } = renderHook(() => usePortManager(nodes, links));

      const usedOnHub = result.current.getUsedInterfaces("hub");
      expect(usedOnHub.size).toBe(10);

      const nextInterface = result.current.getNextInterface("hub");
      expect(nextInterface).toBe("eth11");
    });

    it("handles duplicate links gracefully", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "node-1" }),
        createDeviceNode({ id: "node-2" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1",
          targetInterface: "eth1",
        }),
        createLink({
          id: "link-2",
          source: "node-1",
          target: "node-2",
          sourceInterface: "eth1", // Same interface as link-1
          targetInterface: "eth2",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      // Set should deduplicate
      const usedOnNode1 = result.current.getUsedInterfaces("node-1");
      expect(usedOnNode1.has("eth1")).toBe(true);
      expect(usedOnNode1.size).toBe(1);
    });

    it("handles mixed node types", () => {
      const nodes: Node[] = [
        createDeviceNode({ id: "router", model: "ceos" }),
        createExternalNetworkNode({ id: "external" }),
      ];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "router",
          target: "external",
          sourceInterface: "Ethernet1",
          targetInterface: "eth0",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      expect(result.current.getNodeModel("router")).toBe("ceos");
      expect(result.current.getNodeModel("external")).toBe("external");
      expect(result.current.isInterfaceUsed("router", "Ethernet1")).toBe(true);
      expect(result.current.isInterfaceUsed("external", "eth0")).toBe(true);
    });

    it("handles link referencing non-existent node", () => {
      const nodes: Node[] = [createDeviceNode({ id: "node-1" })];
      const links: Link[] = [
        createLink({
          id: "link-1",
          source: "node-1",
          target: "non-existent",
          sourceInterface: "eth1",
          targetInterface: "eth1",
        }),
      ];

      const { result } = renderHook(() => usePortManager(nodes, links));

      // Should still track the source interface
      expect(result.current.isInterfaceUsed("node-1", "eth1")).toBe(true);
      // Non-existent node should have empty used set
      expect(result.current.getUsedInterfaces("non-existent").size).toBe(0);
    });
  });
});
