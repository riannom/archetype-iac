import React, { useEffect, useMemo, useState, useCallback } from 'react';
import type { InfraViewProps, HostGroup, NodeWithState, HostStats, AgentGraphNode, CrossHostBundle } from './types';
import { LinkStateData } from '../../hooks/useLabStateWS';
import { isDeviceNode } from '../../types';
import { getAgentColor } from '../../../utils/agentColors';
import { getLabInterfaceMappings } from '../../../api';
import InfraHeader from './InfraHeader';
import AgentGraph from './AgentGraph';
import DetailPanel from './DetailPanel';
import VniLinkDetailPanel from './VniLinkDetailPanel';
import LinkTable from './LinkTable';
import NotificationsPanel from './NotificationsPanel';

export { type InfraViewProps } from './types';

const InfraView: React.FC<InfraViewProps> = ({
  labId,
  nodes,
  nodeStates,
  linkStates,
  agents,
  deviceModels,
}) => {
  const [selectedAgentIds, setSelectedAgentIds] = useState<Set<string>>(new Set());
  const [selectedLinkName, setSelectedLinkName] = useState<string | null>(null);
  const [vendorLookup, setVendorLookup] = useState<Map<string, string>>(new Map());
  const [bottomTab, setBottomTab] = useState<'links' | 'notifications'>('links');
  const [notifRefreshKey, setNotifRefreshKey] = useState(0);

  // Fetch interface mappings and build vendor lookup
  useEffect(() => {
    let cancelled = false;

    getLabInterfaceMappings(labId)
      .then(({ mappings }) => {
        if (cancelled) return;

        // Build lookup: "containerName:linuxInterface" → vendor_interface
        // node_name from API is the container_name, matching link_state source_node/target_node
        const lookup = new Map<string, string>();
        for (const m of mappings) {
          if (!m.vendor_interface || !m.node_name) continue;
          lookup.set(`${m.node_name}:${m.linux_interface}`, m.vendor_interface);
        }

        setVendorLookup(lookup);
      })
      .catch(() => {
        // Silently fail — table will just show linux interface names
      });

    return () => { cancelled = true; };
  }, [labId]);

  // Group nodes by host (same logic as before)
  const hostGroups = useMemo((): HostGroup[] => {
    const agentMap = new Map(agents.map(a => [a.id, a]));
    const groups = new Map<string, { nodes: NodeWithState[]; hostName: string; agentId: string }>();
    const unassigned: NodeWithState[] = [];

    for (const node of nodes) {
      const guiId = node.id;
      const state = nodeStates[guiId] || null;
      const containerName = isDeviceNode(node) ? (node.container_name || node.name) : node.name;
      const nws: NodeWithState = { node, state, containerName };

      const hostId = state?.host_id;
      if (!hostId) {
        unassigned.push(nws);
        continue;
      }

      if (!groups.has(hostId)) {
        const agent = agentMap.get(hostId);
        groups.set(hostId, {
          nodes: [],
          hostName: state?.host_name || agent?.name || hostId.slice(0, 8),
          agentId: hostId,
        });
      }
      groups.get(hostId)!.nodes.push(nws);
    }

    const result: HostGroup[] = [];

    for (const [hostId, group] of groups) {
      const containerNames = new Set(group.nodes.map(n => n.containerName));
      const localLinks: LinkStateData[] = [];
      const vlanTags = new Set<number>();

      linkStates.forEach((ls) => {
        if (ls.is_cross_host) return;
        if (containerNames.has(ls.source_node) || containerNames.has(ls.target_node)) {
          localLinks.push(ls);
          if (ls.source_vlan_tag != null) vlanTags.add(ls.source_vlan_tag);
          if (ls.target_vlan_tag != null) vlanTags.add(ls.target_vlan_tag);
        }
      });

      const runningCount = group.nodes.filter(n => n.state?.actual_state === 'running').length;

      const stats: HostStats = {
        nodeCount: group.nodes.length,
        runningCount,
        linkCount: localLinks.length,
        vlanTags,
      };

      result.push({
        hostId,
        hostName: group.hostName,
        agentId: group.agentId,
        nodes: group.nodes,
        localLinks,
        stats,
      });
    }

    if (unassigned.length > 0) {
      result.push({
        hostId: '__unassigned__',
        hostName: 'Unassigned',
        agentId: '',
        nodes: unassigned,
        localLinks: [],
        stats: {
          nodeCount: unassigned.length,
          runningCount: unassigned.filter(n => n.state?.actual_state === 'running').length,
          linkCount: 0,
          vlanTags: new Set(),
        },
      });
    }

    return result;
  }, [nodes, nodeStates, linkStates, agents]);

  // Filter cross-host links
  const crossHostLinks = useMemo((): LinkStateData[] => {
    const result: LinkStateData[] = [];
    linkStates.forEach((ls) => {
      if (ls.is_cross_host) result.push(ls);
    });
    return result;
  }, [linkStates]);

  // Derive AgentGraphNodes from hostGroups
  const agentGraphNodes = useMemo((): AgentGraphNode[] => {
    return hostGroups.map(group => ({
      agentId: group.agentId || group.hostId,
      agentName: group.hostName,
      color: group.agentId ? getAgentColor(group.agentId) : '#a8a29e',
      nodes: group.nodes,
      localLinks: group.localLinks,
      stats: group.stats,
    }));
  }, [hostGroups]);

  // Derive set of container names involved in cross-host links
  const crossHostNodeNames = useMemo((): Set<string> => {
    const names = new Set<string>();
    crossHostLinks.forEach(ls => {
      names.add(ls.source_node);
      names.add(ls.target_node);
    });
    return names;
  }, [crossHostLinks]);

  // Group cross-host links into bundles by agent pair
  const crossHostBundles = useMemo((): CrossHostBundle[] => {
    const bundleMap = new Map<string, LinkStateData[]>();

    for (const ls of crossHostLinks) {
      const a = ls.source_host_id || '';
      const b = ls.target_host_id || '';
      if (!a || !b) continue;
      const key = [a, b].sort().join('|');
      if (!bundleMap.has(key)) bundleMap.set(key, []);
      bundleMap.get(key)!.push(ls);
    }

    const bundles: CrossHostBundle[] = [];
    for (const [key, links] of bundleMap) {
      const [agentA, agentB] = key.split('|');
      bundles.push({
        agentA,
        agentB,
        links,
        hasError: links.some(l => l.actual_state === 'error'),
        allUp: links.every(l => l.actual_state === 'up'),
      });
    }

    return bundles;
  }, [crossHostLinks]);

  // Aggregate stats for header
  const totalNodes = hostGroups.reduce((sum, g) => sum + g.stats.nodeCount, 0);
  const totalRunning = hostGroups.reduce((sum, g) => sum + g.stats.runningCount, 0);
  const allVlanTags = useMemo(() => {
    const tags = new Set<number>();
    hostGroups.forEach(g => g.stats.vlanTags.forEach(t => tags.add(t)));
    crossHostLinks.forEach(ls => {
      if (ls.source_vlan_tag != null) tags.add(ls.source_vlan_tag);
      if (ls.target_vlan_tag != null) tags.add(ls.target_vlan_tag);
      if (ls.vni != null) tags.add(ls.vni);
    });
    return tags;
  }, [hostGroups, crossHostLinks]);

  // Selection handlers
  const handleSelectAgent = useCallback((agentId: string, multi: boolean) => {
    setSelectedLinkName(null);
    setSelectedAgentIds(prev => {
      const next = new Set(multi ? prev : []);
      if (next.has(agentId)) {
        next.delete(agentId);
      } else {
        next.add(agentId);
      }
      return next;
    });
  }, []);

  const handleDeselectAll = useCallback(() => {
    setSelectedAgentIds(new Set());
  }, []);

  const handleSelectLink = useCallback((linkName: string | null) => {
    setSelectedLinkName(linkName);
    if (linkName) {
      setSelectedAgentIds(new Set());
    }
  }, []);

  const handleCloseLinkDetail = useCallback(() => {
    setSelectedLinkName(null);
  }, []);

  // Find the selected link's LinkStateData (search both cross-host and local)
  const selectedLinkState = useMemo(() => {
    if (!selectedLinkName) return null;
    // Check cross-host first
    const crossHost = crossHostLinks.find(ls => ls.link_name === selectedLinkName);
    if (crossHost) return crossHost;
    // Check local links
    for (const group of hostGroups) {
      const local = group.localLinks.find(ls => ls.link_name === selectedLinkName);
      if (local) return local;
    }
    return null;
  }, [selectedLinkName, crossHostLinks, hostGroups]);

  // Bump notification refresh key when link states change (error additions/removals)
  const linkErrorCount = useMemo(() => {
    let count = 0;
    linkStates.forEach(ls => { if (ls.actual_state === 'error') count++; });
    return count;
  }, [linkStates]);

  useEffect(() => {
    setNotifRefreshKey(prev => prev + 1);
  }, [linkErrorCount]);

  const hasAgentSelection = selectedAgentIds.size > 0;

  return (
    <div className="h-full flex flex-col bg-stone-950 w-full">
      <InfraHeader
        hostGroups={hostGroups}
        crossHostLinks={crossHostLinks}
        totalNodes={totalNodes}
        totalRunning={totalRunning}
        allVlanTags={allVlanTags}
      />

      <AgentGraph
        agentNodes={agentGraphNodes}
        crossHostLinks={crossHostLinks}
        crossHostNodeNames={crossHostNodeNames}
        selectedIds={selectedAgentIds}
        onSelectAgent={handleSelectAgent}
        onDeselectAll={handleDeselectAll}
        selectedLinkName={selectedLinkName}
        onSelectLink={handleSelectLink}
        vendorLookup={vendorLookup}
        deviceModels={deviceModels}
      />

      {selectedLinkState ? (
        <VniLinkDetailPanel
          labId={labId}
          linkState={selectedLinkState}
          onClose={handleCloseLinkDetail}
        />
      ) : hasAgentSelection ? (
        <DetailPanel
          selectedIds={selectedAgentIds}
          agentNodes={agentGraphNodes}
          crossHostBundles={crossHostBundles}
          onClose={handleDeselectAll}
        />
      ) : (
        <div className="border-t border-stone-700/50 bg-stone-900/90 backdrop-blur-xl flex flex-col overflow-hidden"
          style={{ maxHeight: '45%' }}
        >
          {/* Tab bar */}
          <div className="flex items-center gap-0 border-b border-stone-800/50 flex-shrink-0">
            <button
              onClick={() => setBottomTab('links')}
              className={`px-4 py-2 text-[10px] font-bold uppercase tracking-wider border-b-2 transition-colors ${
                bottomTab === 'links'
                  ? 'text-stone-200 border-stone-400'
                  : 'text-stone-500 border-transparent hover:text-stone-300'
              }`}
            >
              Links
            </button>
            <button
              onClick={() => setBottomTab('notifications')}
              className={`px-4 py-2 text-[10px] font-bold uppercase tracking-wider border-b-2 transition-colors ${
                bottomTab === 'notifications'
                  ? 'text-stone-200 border-stone-400'
                  : 'text-stone-500 border-transparent hover:text-stone-300'
              }`}
            >
              Notifications
            </button>
          </div>

          {/* Tab content */}
          {bottomTab === 'links' ? (
            <LinkTable
              hostGroups={hostGroups}
              crossHostLinks={crossHostLinks}
              vendorLookup={vendorLookup}
              selectedLinkName={selectedLinkName}
              onSelectLink={(name) => handleSelectLink(name)}
            />
          ) : (
            <NotificationsPanel
              labId={labId}
              refreshKey={notifRefreshKey}
            />
          )}
        </div>
      )}
    </div>
  );
};

export default InfraView;
