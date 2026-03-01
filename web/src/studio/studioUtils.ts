import { DeviceModel, DeviceType, Node, DeviceNode, ExternalNetworkNode, Link } from './types';
import { TopologyGraph } from '../types';

/**
 * Generate a container name from a display name.
 * Container names must be valid for containerlab (lowercase, alphanumeric + underscore).
 * This name is immutable after first creation - display names can change freely.
 */
export const generateContainerName = (displayName: string): string => {
  return displayName
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, '_')  // Replace invalid chars with underscore
    .replace(/^[^a-z_]/, 'n')     // Ensure starts with letter or underscore
    .replace(/_+/g, '_')          // Collapse multiple underscores
    .substring(0, 20);            // Limit length
};

export const buildGraphNodes = (graph: TopologyGraph, models: DeviceModel[]): Node[] => {
  const modelMap = new Map(models.map((model) => [model.id, model]));
  return graph.nodes.map((node, index) => {
    const column = index % 5;
    const row = Math.floor(index / 5);

    // Handle external network nodes
    if ((node as any).node_type === 'external') {
      const extNode: ExternalNetworkNode = {
        id: node.id,
        nodeType: 'external',
        name: node.name || node.id,
        managedInterfaceId: (node as any).managed_interface_id,
        managedInterfaceName: (node as any).managed_interface_name,
        managedInterfaceHostId: (node as any).managed_interface_host_id,
        managedInterfaceHostName: (node as any).managed_interface_host_name,
        connectionType: (node as any).connection_type || undefined,
        parentInterface: (node as any).parent_interface,
        vlanId: (node as any).vlan_id,
        bridgeName: (node as any).bridge_name,
        host: (node as any).host,
        x: 220 + column * 160,
        y: 180 + row * 140,
      };
      return extNode;
    }

    // Handle device nodes
    const modelId = node.device || node.id;
    const model = modelMap.get(modelId);
    const deviceNode: DeviceNode = {
      id: node.id,
      nodeType: 'device',
      name: node.name || node.id,
      container_name: node.container_name || undefined, // Preserve container_name from backend
      type: model?.type || DeviceType.CONTAINER,
      model: model?.id || modelId,
      version: node.version || model?.versions?.[0] || 'default',
      x: 220 + column * 160,
      y: 180 + row * 140,
      cpu: node.cpu || model?.cpu || 1,
      memory: node.memory || model?.memory || 1024,
      disk_driver: node.disk_driver ?? undefined,
      nic_driver: node.nic_driver ?? undefined,
      machine_type: node.machine_type ?? undefined,
      host: (node as any).host, // Preserve host from backend for multi-host placement
    };
    return deviceNode;
  });
};

export const buildGraphLinks = (graph: TopologyGraph): Link[] => {
  return graph.links
    .map((link, index) => {
      if (!link.endpoints || link.endpoints.length < 2) return null;
      const [source, target] = link.endpoints;
      return {
        id: `link-${index}-${source.node}-${target.node}`,
        source: source.node,
        target: target.node,
        type: 'p2p',
        sourceInterface: source.ifname || undefined,
        targetInterface: target.ifname || undefined,
      };
    })
    .filter(Boolean) as Link[];
};
