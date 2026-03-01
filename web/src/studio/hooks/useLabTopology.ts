import { useCallback, useEffect, useRef, useState } from 'react';
import { Annotation, AnnotationType, DeviceModel, LabLayout, Link, Node, isExternalNetworkNode } from '../types';
import { TopologyGraph } from '../../types';
import { buildGraphNodes, buildGraphLinks } from '../studioUtils';
import { TaskLogEntry } from '../components/TaskLogPanel';

interface LabSummary {
  id: string;
  name: string;
  created_at?: string;
  node_count?: number;
  running_count?: number;
  container_count?: number;
  vm_count?: number;
}

interface UseLabTopologyOptions {
  activeLab: LabSummary | null;
  deviceModels: DeviceModel[];
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  addTaskLogEntry: (level: TaskLogEntry['level'], message: string, jobId?: string) => void;
}

export function useLabTopology({
  activeLab,
  deviceModels,
  studioRequest,
  addTaskLogEntry,
}: UseLabTopologyOptions) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);

  const layoutDirtyRef = useRef(false);
  const saveLayoutTimeoutRef = useRef<number | null>(null);
  const topologyDirtyRef = useRef(false);
  const saveTopologyTimeoutRef = useRef<number | null>(null);
  // Refs to track current state for debounced saves (avoids stale closure issues)
  const nodesRef = useRef<Node[]>([]);
  const linksRef = useRef<Link[]>([]);
  const annotationsRef = useRef<Annotation[]>([]);

  // Keep refs in sync with state for debounced saves (avoids stale closure issues)
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { linksRef.current = links; }, [links]);
  useEffect(() => { annotationsRef.current = annotations; }, [annotations]);

  // Build current layout from state
  const buildLayoutFromState = useCallback(
    (currentNodes: Node[], currentAnnotations: Annotation[]): LabLayout => {
      const nodeLayouts: Record<string, { x: number; y: number; label?: string; color?: string }> = {};
      currentNodes.forEach((node) => {
        nodeLayouts[node.id] = {
          x: node.x,
          y: node.y,
          label: node.label,
        };
      });
      return {
        version: 1,
        nodes: nodeLayouts,
        annotations: currentAnnotations.map((ann) => ({
          id: ann.id,
          type: ann.type,
          x: ann.x,
          y: ann.y,
          width: ann.width,
          height: ann.height,
          text: ann.text,
          color: ann.color,
          fontSize: ann.fontSize,
          targetX: ann.targetX,
          targetY: ann.targetY,
          zIndex: ann.zIndex,
        })),
      };
    },
    []
  );

  // Save layout to backend (debounced)
  const saveLayout = useCallback(
    async (labId: string, currentNodes: Node[], currentAnnotations: Annotation[]) => {
      if (currentNodes.length === 0) return;
      const layout = buildLayoutFromState(currentNodes, currentAnnotations);
      try {
        await studioRequest(`/labs/${labId}/layout`, {
          method: 'PUT',
          body: JSON.stringify(layout),
        });
        layoutDirtyRef.current = false;
      } catch (error) {
        console.error('Failed to save layout:', error);
      }
    },
    [buildLayoutFromState, studioRequest]
  );

  // Trigger debounced layout save
  // Uses refs to read current state at save time, avoiding stale closure issues
  const triggerLayoutSave = useCallback(() => {
    if (!activeLab) return;
    layoutDirtyRef.current = true;
    if (saveLayoutTimeoutRef.current) {
      window.clearTimeout(saveLayoutTimeoutRef.current);
    }
    const labId = activeLab.id;
    saveLayoutTimeoutRef.current = window.setTimeout(() => {
      if (layoutDirtyRef.current) {
        // Read current state from refs to get latest values
        saveLayout(labId, nodesRef.current, annotationsRef.current);
      }
    }, 500);
  }, [activeLab, saveLayout]);

  // Save topology to backend (auto-save on changes)
  const saveTopology = useCallback(
    async (labId: string, currentNodes: Node[], currentLinks: Link[], rethrowOnError = false) => {
      if (currentNodes.length === 0) return;
      const graph: TopologyGraph = {
        nodes: currentNodes.map((node) => {
          // Handle external network nodes
          if (isExternalNetworkNode(node)) {
            return {
              id: node.id,
              name: node.name,
              node_type: 'external',
              managed_interface_id: node.managedInterfaceId,
              connection_type: node.connectionType,
              parent_interface: node.parentInterface,
              vlan_id: node.vlanId,
              bridge_name: node.bridgeName,
              host: node.host,
            };
          }
          // Handle device nodes
          const deviceNode = node as import('../types').DeviceNode;
          return {
            id: node.id,
            name: node.name,
            node_type: 'device',
            // Include container_name for backend container identity (immutable after first save)
            container_name: deviceNode.container_name,
            device: deviceNode.model,
            version: deviceNode.version,
            host: deviceNode.host,
            // Hardware spec overrides (only include when explicitly set by user)
            cpu: deviceNode.cpu,
            memory: deviceNode.memory,
            ...(deviceNode.disk_driver ? { disk_driver: deviceNode.disk_driver } : {}),
            ...(deviceNode.nic_driver ? { nic_driver: deviceNode.nic_driver } : {}),
            ...(deviceNode.machine_type ? { machine_type: deviceNode.machine_type } : {}),
          };
        }),
        links: currentLinks.map((link) => ({
          endpoints: [
            { node: link.source, ifname: link.sourceInterface },
            { node: link.target, ifname: link.targetInterface },
          ],
        })),
      };
      try {
        await studioRequest(`/labs/${labId}/update-topology`, {
          method: 'POST',
          body: JSON.stringify(graph),
        });
        topologyDirtyRef.current = false;
        addTaskLogEntry('info', 'Topology auto-saved');
      } catch (error) {
        console.error('Failed to save topology:', error);
        if (rethrowOnError) {
          throw error;
        }
      }
    },
    [studioRequest, addTaskLogEntry]
  );

  // Trigger debounced topology save
  // Uses refs to read current state at save time, avoiding stale closure issues
  const triggerTopologySave = useCallback(() => {
    if (!activeLab) return;
    topologyDirtyRef.current = true;
    if (saveTopologyTimeoutRef.current) {
      window.clearTimeout(saveTopologyTimeoutRef.current);
    }
    const labId = activeLab.id;
    saveTopologyTimeoutRef.current = window.setTimeout(() => {
      if (topologyDirtyRef.current) {
        // Read current state from refs to get latest values
        saveTopology(labId, nodesRef.current, linksRef.current);
      }
    }, 2000); // 2 second debounce for topology saves
  }, [activeLab, saveTopology]);

  // Flush any pending topology save immediately (for use before deploy)
  // Returns a promise that resolves when the save is complete
  const flushTopologySave = useCallback(async () => {
    if (!activeLab) return;
    // Cancel pending debounced save
    if (saveTopologyTimeoutRef.current) {
      window.clearTimeout(saveTopologyTimeoutRef.current);
      saveTopologyTimeoutRef.current = null;
    }
    // If dirty, save immediately
    if (topologyDirtyRef.current) {
      await saveTopology(activeLab.id, nodesRef.current, linksRef.current, true);
    }
  }, [activeLab, saveTopology]);

  const loadLayout = useCallback(async (labId: string): Promise<LabLayout | null> => {
    try {
      return await studioRequest<LabLayout>(`/labs/${labId}/layout`);
    } catch {
      // Layout not found is expected for new labs
      return null;
    }
  }, [studioRequest]);

  const loadGraph = useCallback(async (labId: string) => {
    try {
      const graph = await studioRequest<TopologyGraph>(`/labs/${labId}/export-graph`);
      const layout = await loadLayout(labId);

      // Build nodes with layout positions if available
      let newNodes = buildGraphNodes(graph, deviceModels);
      if (layout?.nodes) {
        newNodes = newNodes.map((node) => {
          const nodeLayout = layout.nodes[node.id];
          if (nodeLayout) {
            return {
              ...node,
              x: nodeLayout.x,
              y: nodeLayout.y,
              label: nodeLayout.label ?? node.label,
            };
          }
          return node;
        });
      }
      setNodes(newNodes);
      setLinks(buildGraphLinks(graph));

      // Restore annotations from layout
      if (layout?.annotations && layout.annotations.length > 0) {
        setAnnotations(
          layout.annotations.map((ann) => ({
            id: ann.id,
            type: (ann.type === 'caption' ? 'text' : ann.type) as AnnotationType,
            x: ann.x,
            y: ann.y,
            width: ann.width,
            height: ann.height,
            text: ann.text,
            color: ann.color,
            fontSize: ann.fontSize,
            targetX: ann.targetX,
            targetY: ann.targetY,
            zIndex: ann.zIndex,
          }))
        );
      } else {
        setAnnotations([]);
      }

      layoutDirtyRef.current = false;
    } catch {
      // New lab with no topology - clear state
      setNodes([]);
      setLinks([]);
      setAnnotations([]);
      layoutDirtyRef.current = false;
    }
  }, [deviceModels, studioRequest, loadLayout]);

  // Load graph when active lab changes
  useEffect(() => {
    if (!activeLab) return;
    loadGraph(activeLab.id);
  }, [activeLab, loadGraph]);

  // Cleanup: save layout/topology and clear timeouts on unmount
  useEffect(() => {
    return () => {
      if (saveLayoutTimeoutRef.current) {
        window.clearTimeout(saveLayoutTimeoutRef.current);
      }
      if (saveTopologyTimeoutRef.current) {
        window.clearTimeout(saveTopologyTimeoutRef.current);
      }
    };
  }, []);

  return {
    nodes,
    setNodes,
    links,
    setLinks,
    annotations,
    setAnnotations,
    nodesRef,
    linksRef,
    layoutDirtyRef,
    saveLayout,
    triggerLayoutSave,
    triggerTopologySave,
    flushTopologySave,
    saveTopology,
    loadGraph,
  };
}
