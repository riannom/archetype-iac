/**
 * Zustand store for canvas state management.
 *
 * This store provides centralized state management for the studio canvas,
 * including nodes, links, selection, and runtime states. It replaces
 * scattered useState hooks with a single source of truth.
 *
 * Features:
 * - Centralized node and link state
 * - Selection management
 * - Runtime state tracking (from WebSocket)
 * - Optimistic updates support
 * - TypeScript support with full typing
 *
 * Usage:
 * ```tsx
 * import { useCanvasStore } from './store/canvasStore';
 *
 * function MyComponent() {
 *   const { nodes, addNode, selectNode } = useCanvasStore();
 *   // ...
 * }
 * ```
 */

import { create } from 'zustand';
import { devtools, subscribeWithSelector } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { enableMapSet } from 'immer';
import { Node, Link, DeviceNode, ExternalNetworkNode } from '../types';
import { NodeStateData } from '../../types/nodeState';

enableMapSet();

export type { NodeStateData };

export interface LinkStateData {
  link_name: string;
  desired_state: 'up' | 'down';
  actual_state: 'up' | 'down' | 'pending' | 'error' | 'unknown';
  source_node: string;
  target_node: string;
  error_message?: string | null;
}

export interface LabStateData {
  lab_id: string;
  state: string;
  error?: string | null;
}

// Canvas viewport state
export interface ViewportState {
  x: number;
  y: number;
  zoom: number;
}

// Canvas store state
export interface CanvasState {
  // Lab context
  labId: string | null;

  // Data
  nodes: Map<string, Node>;
  links: Map<string, Link>;

  // Runtime state (from WebSocket)
  nodeStates: Map<string, NodeStateData>;
  linkStates: Map<string, LinkStateData>;
  labState: LabStateData | null;

  // Selection
  selectedNodeIds: Set<string>;
  selectedLinkIds: Set<string>;

  // UI state
  viewport: ViewportState;
  isDirty: boolean;
  isConnected: boolean;

  // Actions - Lab
  setLabId: (labId: string | null) => void;

  // Actions - Nodes
  addNode: (node: Node) => void;
  updateNode: (id: string, updates: Partial<Node>) => void;
  removeNode: (id: string) => void;
  setNodes: (nodes: Node[]) => void;

  // Actions - Links
  addLink: (link: Link) => void;
  updateLink: (id: string, updates: Partial<Link>) => void;
  removeLink: (id: string) => void;
  setLinks: (links: Link[]) => void;

  // Actions - Selection
  selectNode: (id: string, additive?: boolean) => void;
  selectNodes: (ids: string[]) => void;
  selectLink: (id: string, additive?: boolean) => void;
  selectLinks: (ids: string[]) => void;
  clearSelection: () => void;
  selectAll: () => void;

  // Actions - Runtime State (from WebSocket)
  setNodeState: (nodeId: string, state: NodeStateData) => void;
  setNodeStates: (states: NodeStateData[]) => void;
  setLinkState: (linkName: string, state: LinkStateData) => void;
  setLinkStates: (states: LinkStateData[]) => void;
  setLabState: (state: LabStateData | null) => void;
  setConnected: (connected: boolean) => void;

  // Actions - Viewport
  setViewport: (viewport: ViewportState) => void;

  // Actions - Bulk
  importTopology: (nodes: Node[], links: Link[]) => void;
  reset: () => void;
  setDirty: (dirty: boolean) => void;
}

// Initial state
const initialState = {
  labId: null,
  nodes: new Map<string, Node>(),
  links: new Map<string, Link>(),
  nodeStates: new Map<string, NodeStateData>(),
  linkStates: new Map<string, LinkStateData>(),
  labState: null,
  selectedNodeIds: new Set<string>(),
  selectedLinkIds: new Set<string>(),
  viewport: { x: 0, y: 0, zoom: 1 },
  isDirty: false,
  isConnected: false,
};

/**
 * Main canvas store using Zustand.
 *
 * Uses immer for immutable updates, devtools for debugging,
 * and subscribeWithSelector for granular subscriptions.
 */
export const useCanvasStore = create<CanvasState>()(
  devtools(
    subscribeWithSelector(
      immer((set, get) => ({
        ...initialState,

        // --- Lab Actions ---

        setLabId: (labId) =>
          set((state) => {
            // Reset state when lab changes
            if (state.labId !== labId) {
              state.labId = labId;
              state.nodes.clear();
              state.links.clear();
              state.nodeStates.clear();
              state.linkStates.clear();
              state.labState = null;
              state.selectedNodeIds.clear();
              state.selectedLinkIds.clear();
              state.isDirty = false;
            }
          }),

        // --- Node Actions ---

        addNode: (node) =>
          set((state) => {
            state.nodes.set(node.id, node);
            state.isDirty = true;
          }),

        updateNode: (id, updates) =>
          set((state) => {
            const node = state.nodes.get(id);
            if (node) {
              state.nodes.set(id, { ...node, ...updates } as Node);
              state.isDirty = true;
            }
          }),

        removeNode: (id) =>
          set((state) => {
            state.nodes.delete(id);
            state.selectedNodeIds.delete(id);
            state.nodeStates.delete(id);
            // Also remove links connected to this node
            for (const [linkId, link] of state.links) {
              if (link.source === id || link.target === id) {
                state.links.delete(linkId);
                state.selectedLinkIds.delete(linkId);
              }
            }
            state.isDirty = true;
          }),

        setNodes: (nodes) =>
          set((state) => {
            state.nodes.clear();
            for (const node of nodes) {
              state.nodes.set(node.id, node);
            }
          }),

        // --- Link Actions ---

        addLink: (link) =>
          set((state) => {
            state.links.set(link.id, link);
            state.isDirty = true;
          }),

        updateLink: (id, updates) =>
          set((state) => {
            const link = state.links.get(id);
            if (link) {
              state.links.set(id, { ...link, ...updates });
              state.isDirty = true;
            }
          }),

        removeLink: (id) =>
          set((state) => {
            state.links.delete(id);
            state.selectedLinkIds.delete(id);
            state.linkStates.delete(id);
            state.isDirty = true;
          }),

        setLinks: (links) =>
          set((state) => {
            state.links.clear();
            for (const link of links) {
              state.links.set(link.id, link);
            }
          }),

        // --- Selection Actions ---

        selectNode: (id, additive = false) =>
          set((state) => {
            if (!additive) {
              state.selectedNodeIds.clear();
              state.selectedLinkIds.clear();
            }
            if (state.nodes.has(id)) {
              state.selectedNodeIds.add(id);
            }
          }),

        selectNodes: (ids) =>
          set((state) => {
            state.selectedNodeIds.clear();
            state.selectedLinkIds.clear();
            for (const id of ids) {
              if (state.nodes.has(id)) {
                state.selectedNodeIds.add(id);
              }
            }
          }),

        selectLink: (id, additive = false) =>
          set((state) => {
            if (!additive) {
              state.selectedNodeIds.clear();
              state.selectedLinkIds.clear();
            }
            if (state.links.has(id)) {
              state.selectedLinkIds.add(id);
            }
          }),

        selectLinks: (ids) =>
          set((state) => {
            state.selectedNodeIds.clear();
            state.selectedLinkIds.clear();
            for (const id of ids) {
              if (state.links.has(id)) {
                state.selectedLinkIds.add(id);
              }
            }
          }),

        clearSelection: () =>
          set((state) => {
            state.selectedNodeIds.clear();
            state.selectedLinkIds.clear();
          }),

        selectAll: () =>
          set((state) => {
            state.selectedNodeIds = new Set(state.nodes.keys());
            state.selectedLinkIds = new Set(state.links.keys());
          }),

        // --- Runtime State Actions ---

        setNodeState: (nodeId, nodeState) =>
          set((state) => {
            state.nodeStates.set(nodeId, nodeState);
          }),

        setNodeStates: (states) =>
          set((state) => {
            state.nodeStates.clear();
            for (const ns of states) {
              state.nodeStates.set(ns.node_id, ns);
            }
          }),

        setLinkState: (linkName, linkState) =>
          set((state) => {
            state.linkStates.set(linkName, linkState);
          }),

        setLinkStates: (states) =>
          set((state) => {
            state.linkStates.clear();
            for (const ls of states) {
              state.linkStates.set(ls.link_name, ls);
            }
          }),

        setLabState: (labState) =>
          set((state) => {
            state.labState = labState;
          }),

        setConnected: (connected) =>
          set((state) => {
            state.isConnected = connected;
          }),

        // --- Viewport Actions ---

        setViewport: (viewport) =>
          set((state) => {
            state.viewport = viewport;
          }),

        // --- Bulk Actions ---

        importTopology: (nodes, links) =>
          set((state) => {
            state.nodes.clear();
            state.links.clear();
            state.selectedNodeIds.clear();
            state.selectedLinkIds.clear();
            for (const node of nodes) {
              state.nodes.set(node.id, node);
            }
            for (const link of links) {
              state.links.set(link.id, link);
            }
            state.isDirty = false;
          }),

        reset: () =>
          set((state) => {
            Object.assign(state, initialState);
            state.nodes = new Map();
            state.links = new Map();
            state.nodeStates = new Map();
            state.linkStates = new Map();
            state.selectedNodeIds = new Set();
            state.selectedLinkIds = new Set();
          }),

        setDirty: (dirty) =>
          set((state) => {
            state.isDirty = dirty;
          }),
      }))
    ),
    { name: 'canvas-store' }
  )
);

// --- Selector Hooks ---

/**
 * Get all nodes as an array.
 */
export const useNodes = () =>
  useCanvasStore((state) => Array.from(state.nodes.values()));

/**
 * Get all links as an array.
 */
export const useLinks = () =>
  useCanvasStore((state) => Array.from(state.links.values()));

/**
 * Get selected node IDs.
 */
export const useSelectedNodeIds = () =>
  useCanvasStore((state) => state.selectedNodeIds);

/**
 * Get selected nodes.
 */
export const useSelectedNodes = () =>
  useCanvasStore((state) => {
    const nodes: Node[] = [];
    for (const id of state.selectedNodeIds) {
      const node = state.nodes.get(id);
      if (node) nodes.push(node);
    }
    return nodes;
  });

/**
 * Get node state for a specific node.
 */
export const useNodeState = (nodeId: string) =>
  useCanvasStore((state) => state.nodeStates.get(nodeId));

/**
 * Get all node states as an object.
 */
export const useNodeStates = () =>
  useCanvasStore((state) => {
    const obj: Record<string, NodeStateData> = {};
    for (const [id, ns] of state.nodeStates) {
      obj[id] = ns;
    }
    return obj;
  });

/**
 * Check if canvas has unsaved changes.
 */
export const useIsDirty = () => useCanvasStore((state) => state.isDirty);

/**
 * Check if WebSocket is connected.
 */
export const useIsConnected = () => useCanvasStore((state) => state.isConnected);
