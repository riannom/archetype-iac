import { useMemo, useCallback } from 'react';
import { Node, Link } from '../types';
import {
  getAvailableInterfaces,
  getNextAvailableInterface,
} from '../utils/interfaceRegistry';

export interface PortManager {
  /** Get used interfaces for a specific node */
  getUsedInterfaces: (nodeId: string) => Set<string>;
  /** Get available (unused) interfaces for a node */
  getAvailableInterfaces: (nodeId: string, count?: number) => string[];
  /** Get the next available interface for a node */
  getNextInterface: (nodeId: string) => string;
  /** Check if a specific interface is used on a node */
  isInterfaceUsed: (nodeId: string, ifname: string) => boolean;
  /** Get the model ID for a node */
  getNodeModel: (nodeId: string) => string;
}

/**
 * Hook to manage port/interface allocation across nodes and links.
 *
 * This hook tracks which interfaces are used on each node based on
 * existing links, and provides methods to find available interfaces
 * for new connections.
 */
export function usePortManager(nodes: Node[], links: Link[]): PortManager {
  // Build a map of nodeId -> model for quick lookups
  const nodeModelMap = useMemo(() => {
    const map = new Map<string, string>();
    nodes.forEach((node) => {
      map.set(node.id, node.model);
    });
    return map;
  }, [nodes]);

  // Build a map of nodeId -> Set<used interface names>
  const usedInterfacesByNode = useMemo(() => {
    const map = new Map<string, Set<string>>();

    // Initialize sets for all nodes
    nodes.forEach((node) => {
      map.set(node.id, new Set<string>());
    });

    // Populate with interfaces from existing links
    links.forEach((link) => {
      if (link.sourceInterface) {
        const sourceSet = map.get(link.source);
        if (sourceSet) {
          sourceSet.add(link.sourceInterface);
        }
      }
      if (link.targetInterface) {
        const targetSet = map.get(link.target);
        if (targetSet) {
          targetSet.add(link.targetInterface);
        }
      }
    });

    return map;
  }, [nodes, links]);

  const getNodeModel = useCallback(
    (nodeId: string): string => {
      return nodeModelMap.get(nodeId) || 'generic';
    },
    [nodeModelMap]
  );

  const getUsedInterfaces = useCallback(
    (nodeId: string): Set<string> => {
      return usedInterfacesByNode.get(nodeId) || new Set<string>();
    },
    [usedInterfacesByNode]
  );

  const getAvailableInterfacesForNode = useCallback(
    (nodeId: string, count: number = 10): string[] => {
      const modelId = getNodeModel(nodeId);
      const usedSet = getUsedInterfaces(nodeId);
      return getAvailableInterfaces(modelId, usedSet, count);
    },
    [getNodeModel, getUsedInterfaces]
  );

  const getNextInterface = useCallback(
    (nodeId: string): string => {
      const modelId = getNodeModel(nodeId);
      const usedSet = getUsedInterfaces(nodeId);
      return getNextAvailableInterface(modelId, usedSet);
    },
    [getNodeModel, getUsedInterfaces]
  );

  const isInterfaceUsed = useCallback(
    (nodeId: string, ifname: string): boolean => {
      const usedSet = getUsedInterfaces(nodeId);
      return usedSet.has(ifname);
    },
    [getUsedInterfaces]
  );

  return {
    getUsedInterfaces,
    getAvailableInterfaces: getAvailableInterfacesForNode,
    getNextInterface,
    isInterfaceUsed,
    getNodeModel,
  };
}

export default usePortManager;
