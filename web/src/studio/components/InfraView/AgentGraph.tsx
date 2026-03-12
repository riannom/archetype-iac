import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AgentGraphNode, NodeWithState } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';
import type { DeviceModel } from '../../types';
import AgentNode from './AgentNode';
import GraphLink from './GraphLink';
import {
  type Position,
  type AgentPairSummary,
  WORLD_W,
  WORLD_H,
  SAT_DOT_R,
  MAX_VISIBLE_SATELLITES,
  MIN_ZOOM,
  MAX_ZOOM,
  ZOOM_STEP,
  LINK_STATE_COLORS,
  lightenColor,
  computeInitialPositions,
  computeSatellitePositions,
  computeFitView,
} from './agentGraphLayout';

interface HoverTarget {
  type: 'satellite' | 'agent';
  id: string; // containerName for satellite, agentId for agent
  x: number;
  y: number;
}

interface AgentGraphProps {
  agentNodes: AgentGraphNode[];
  crossHostLinks: LinkStateData[];
  crossHostNodeNames: Set<string>;
  selectedIds: Set<string>;
  onSelectAgent: (agentId: string, multi: boolean) => void;
  onDeselectAll: () => void;
  selectedLinkName: string | null;
  onSelectLink: (linkName: string | null) => void;
  vendorLookup: Map<string, string>;
  deviceModels: DeviceModel[];
}

const AgentGraph: React.FC<AgentGraphProps> = ({
  agentNodes,
  crossHostLinks,
  crossHostNodeNames,
  selectedIds,
  onSelectAgent,
  onDeselectAll,
  selectedLinkName,
  onSelectLink,
  vendorLookup,
  deviceModels,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 500 });
  const positionsRef = useRef<Map<string, Position>>(new Map());
  const [, forceUpdate] = useState(0);
  const initializedRef = useRef(false);
  const prevAgentIdsRef = useRef<string>('');
  const hintDismissed = useRef(false);
  const [showHint, setShowHint] = useState(true);

  // Hover tooltip state
  const [hoverTarget, setHoverTarget] = useState<HoverTarget | null>(null);

  // Viewport: pan offset in world coordinates + zoom level
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });

  // Pan state
  const panning = useRef(false);
  const panStart = useRef({ x: 0, y: 0 });
  const panOrigin = useRef({ x: 0, y: 0 });

  const dismissHint = useCallback(() => {
    if (!hintDismissed.current) {
      hintDismissed.current = true;
      setShowHint(false);
    }
  }, []);

  // Track dimensions via ResizeObserver
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          setDimensions({ width, height });
        }
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Initialize/reset positions when agents change
  useEffect(() => {
    const agentIds = agentNodes.map(a => a.agentId).sort().join(',');
    if (!initializedRef.current || agentIds !== prevAgentIdsRef.current) {
      positionsRef.current = computeInitialPositions(agentNodes);
      prevAgentIdsRef.current = agentIds;
      initializedRef.current = true;
      const fit = computeFitView(positionsRef.current, dimensions);
      setZoom(fit.zoom);
      setPan(fit.pan);
      forceUpdate(n => n + 1);
    }
  }, [agentNodes, dimensions]);

  // Compute satellite positions whenever agent positions or nodes change
  const allPositions = useMemo(() => {
    const positions = new Map(positionsRef.current);
    for (const agent of agentNodes) {
      const agentPos = positionsRef.current.get(agent.agentId);
      if (!agentPos) continue;
      const satPositions = computeSatellitePositions(agentPos, agent.nodes, crossHostNodeNames);
      satPositions.forEach((pos, key) => positions.set(key, pos));
    }
    return positions;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentNodes, crossHostNodeNames, positionsRef.current]);

  // Agent pair summaries for solid host-to-host lines
  const agentPairs = useMemo((): AgentPairSummary[] => {
    const pairMap = new Map<string, LinkStateData[]>();
    for (const ls of crossHostLinks) {
      const a = ls.source_host_id || '';
      const b = ls.target_host_id || '';
      if (!a || !b) continue;
      const key = [a, b].sort().join('|');
      if (!pairMap.has(key)) pairMap.set(key, []);
      pairMap.get(key)!.push(ls);
    }

    const pairs: AgentPairSummary[] = [];
    for (const [key, links] of pairMap) {
      const [agentA, agentB] = key.split('|');
      pairs.push({
        agentA,
        agentB,
        count: links.length,
        hasError: links.some(l => l.actual_state === 'error'),
        allUp: links.every(l => l.actual_state === 'up'),
        hasPending: links.some(l => l.actual_state === 'pending'),
      });
    }
    return pairs;
  }, [crossHostLinks]);

  // Compute the viewBox from zoom + pan
  const viewBox = useMemo(() => {
    const vw = dimensions.width / zoom;
    const vh = dimensions.height / zoom;
    const vx = (WORLD_W - vw) / 2 - pan.x;
    const vy = (WORLD_H - vh) / 2 - pan.y;
    return `${vx} ${vy} ${vw} ${vh}`;
  }, [dimensions, zoom, pan]);

  // Node drag needs to account for zoom: screen px → world units
  const handleDrag = useCallback((agentId: string, dx: number, dy: number) => {
    const pos = positionsRef.current.get(agentId);
    if (!pos) return;
    positionsRef.current.set(agentId, {
      x: pos.x + dx / zoom,
      y: pos.y + dy / zoom,
    });
    dismissHint();
    forceUpdate(n => n + 1);
  }, [zoom, dismissHint]);

  const handleDragEnd = useCallback(() => {}, []);

  // Background pointer events for panning and deselect
  const handleBgPointerDown = useCallback((e: React.PointerEvent) => {
    const target = e.target as SVGElement;
    if (target !== svgRef.current && target.tagName !== 'rect') return;
    panning.current = true;
    panStart.current = { x: e.clientX, y: e.clientY };
    panOrigin.current = { ...pan };
    (e.target as SVGElement).setPointerCapture(e.pointerId);
  }, [pan]);

  const handleBgPointerMove = useCallback((e: React.PointerEvent) => {
    if (!panning.current) return;
    const dx = (e.clientX - panStart.current.x) / zoom;
    const dy = (e.clientY - panStart.current.y) / zoom;
    setPan({ x: panOrigin.current.x + dx, y: panOrigin.current.y + dy });
  }, [zoom]);

  const handleBgPointerUp = useCallback((e: React.PointerEvent) => {
    if (!panning.current) {
      return;
    }
    const dx = Math.abs(e.clientX - panStart.current.x);
    const dy = Math.abs(e.clientY - panStart.current.y);
    (e.target as SVGElement).releasePointerCapture(e.pointerId);
    panning.current = false;
    if (dx + dy >= 3) {
      dismissHint();
    }
    // If it was a click (no movement), deselect
    if (dx + dy < 3) {
      dismissHint();
      onDeselectAll();
      onSelectLink(null);
    }
  }, [onDeselectAll, onSelectLink, dismissHint]);

  // Wheel zoom
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
      setZoom(z => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z + delta)));
    };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, []);

  // Navigator controls
  const handleZoomIn = useCallback(() => {
    setZoom(z => Math.min(MAX_ZOOM, z + ZOOM_STEP));
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoom(z => Math.max(MIN_ZOOM, z - ZOOM_STEP));
  }, []);

  const handleCenter = useCallback(() => {
    setPan({ x: 0, y: 0 });
    setZoom(1);
  }, []);

  const handleFitToScreen = useCallback(() => {
    const fit = computeFitView(positionsRef.current, dimensions);
    setZoom(fit.zoom);
    setPan(fit.pan);
  }, [dimensions]);

  // Satellite overflow counts per agent
  const overflowCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const agent of agentNodes) {
      const total = agent.nodes.length;
      if (total <= MAX_VISIBLE_SATELLITES) continue;
      const crossHostCount = agent.nodes.filter(n => crossHostNodeNames.has(n.containerName)).length;
      const visibleCount = Math.max(crossHostCount, MAX_VISIBLE_SATELLITES);
      const overflow = total - visibleCount;
      if (overflow > 0) counts.set(agent.agentId, overflow);
    }
    return counts;
  }, [agentNodes, crossHostNodeNames]);

  // Sorted visible satellites per agent (for rendering)
  const visibleSatellites = useMemo(() => {
    const result = new Map<string, NodeWithState[]>();
    for (const agent of agentNodes) {
      const sorted = [...agent.nodes].sort((a, b) => {
        const aCross = crossHostNodeNames.has(a.containerName) ? 0 : 1;
        const bCross = crossHostNodeNames.has(b.containerName) ? 0 : 1;
        if (aCross !== bCross) return aCross - bCross;
        return a.node.name.localeCompare(b.node.name);
      });
      const crossHostCount = sorted.filter(n => crossHostNodeNames.has(n.containerName)).length;
      const visibleCount = Math.max(crossHostCount, Math.min(sorted.length, MAX_VISIBLE_SATELLITES));
      result.set(agent.agentId, sorted.slice(0, visibleCount));
    }
    return result;
  }, [agentNodes, crossHostNodeNames]);

  // Build lookup: containerName → NodeWithState + agentName for tooltip
  const satelliteLookup = useMemo(() => {
    const map = new Map<string, { nws: NodeWithState; agentName: string }>();
    for (const agent of agentNodes) {
      for (const nws of agent.nodes) {
        map.set(nws.containerName, { nws, agentName: agent.agentName });
      }
    }
    return map;
  }, [agentNodes]);

  // Build model lookup for device type display
  const modelLookup = useMemo(() => {
    return new Map(deviceModels.map(m => [m.id, m]));
  }, [deviceModels]);

  // Get vendor interfaces for a node
  const getNodeVendorInterfaces = useCallback((containerName: string): string[] => {
    const ifaces: string[] = [];
    vendorLookup.forEach((vendor, key) => {
      if (key.startsWith(`${containerName}:`)) {
        ifaces.push(vendor);
      }
    });
    return ifaces;
  }, [vendorLookup]);

  const handleAgentSelect = useCallback((agentId: string, multi: boolean) => {
    dismissHint();
    onSelectLink(null);
    onSelectAgent(agentId, multi);
  }, [dismissHint, onSelectLink, onSelectAgent]);

  const handleLinkSelect = useCallback((linkName: string) => {
    dismissHint();
    onDeselectAll();
    onSelectLink(linkName);
  }, [dismissHint, onDeselectAll, onSelectLink]);

  const hasSelection = selectedIds.size > 0;
  const positions = positionsRef.current;

  const zoomPercent = Math.round(zoom * 100);

  return (
    <div ref={containerRef} className="flex-1 w-full bg-stone-950 overflow-hidden relative">
      <svg
        ref={svgRef}
        viewBox={viewBox}
        className="w-full h-full"
        style={{ display: 'block', cursor: panning.current ? 'grabbing' : 'default' }}
        onPointerDown={handleBgPointerDown}
        onPointerMove={handleBgPointerMove}
        onPointerUp={handleBgPointerUp}
      >
        {/* Dot grid background pattern */}
        <defs>
          <pattern id="infraGrid" width="20" height="20" patternUnits="userSpaceOnUse">
            <circle cx="10" cy="10" r="0.5" fill="#a8a29e" opacity={0.15} />
          </pattern>
        </defs>
        <rect x="-2000" y="-2000" width="5000" height="5000" fill="url(#infraGrid)" />

        {/* Layer 1: Tether lines from satellites to agent centers */}
        {agentNodes.map((agent) => {
          const agentPos = positions.get(agent.agentId);
          if (!agentPos) return null;
          const sats = visibleSatellites.get(agent.agentId) || [];
          const agentDimmed = hasSelection && !selectedIds.has(agent.agentId);

          return sats.map((nws) => {
            const satPos = allPositions.get(`sat:${nws.containerName}`);
            if (!satPos) return null;
            return (
              <line
                key={`tether:${nws.containerName}`}
                x1={agentPos.x}
                y1={agentPos.y}
                x2={satPos.x}
                y2={satPos.y}
                stroke={lightenColor(agent.color, 0.3)}
                strokeWidth={1.2}
                strokeDasharray="3 3"
                opacity={agentDimmed ? 0.12 : 0.55}
                style={{ transition: 'opacity 300ms ease' }}
              />
            );
          });
        })}

        {/* Layer 2: Solid host-to-host lines (most prominent) */}
        {agentPairs.map((pair) => {
          const posA = positions.get(pair.agentA);
          const posB = positions.get(pair.agentB);
          if (!posA || !posB) return null;

          const bothSelected = selectedIds.has(pair.agentA) && selectedIds.has(pair.agentB);
          const eitherSelected = selectedIds.has(pair.agentA) || selectedIds.has(pair.agentB);
          const pairDimmed = hasSelection && !eitherSelected;
          const pairHighlighted = !hasSelection || bothSelected;

          let color: string;
          if (pair.hasError) color = LINK_STATE_COLORS.error;
          else if (pair.allUp) color = LINK_STATE_COLORS.up;
          else if (pair.hasPending) color = LINK_STATE_COLORS.pending;
          else color = LINK_STATE_COLORS.down;

          const opacity = pairDimmed ? 0.15 : pairHighlighted ? 0.85 : 0.5;
          const strokeWidth = Math.min(2 + pair.count * 0.3, 4);

          const mx = (posA.x + posB.x) / 2;
          const my = (posA.y + posB.y) / 2;

          return (
            <g key={`host-link:${pair.agentA}-${pair.agentB}`} style={{ transition: 'opacity 300ms ease' }} opacity={opacity}>
              <line
                x1={posA.x}
                y1={posA.y}
                x2={posB.x}
                y2={posB.y}
                stroke={color}
                strokeWidth={strokeWidth}
                strokeLinecap="round"
              />
              {/* Link count badge */}
              {pair.count > 1 && (
                <>
                  <rect
                    x={mx - 10}
                    y={my - 8}
                    width={20}
                    height={16}
                    rx={4}
                    fill="#1c1917"
                    stroke={color}
                    strokeWidth={0.5}
                    opacity={0.9}
                  />
                  <text
                    x={mx}
                    y={my + 3}
                    textAnchor="middle"
                    fill={color}
                    fontSize={9}
                    fontFamily="'JetBrains Mono', monospace"
                    style={{ pointerEvents: 'none' }}
                  >
                    {pair.count}
                  </text>
                </>
              )}
            </g>
          );
        })}

        {/* Layer 3: Dashed VNI links between satellite nodes */}
        {crossHostLinks.map((ls) => {
          const srcPos = allPositions.get(`sat:${ls.source_node}`);
          const tgtPos = allPositions.get(`sat:${ls.target_node}`);
          if (!srcPos || !tgtPos) return null;

          const srcAgentId = ls.source_host_id || '';
          const tgtAgentId = ls.target_host_id || '';
          const bothSelected = selectedIds.has(srcAgentId) && selectedIds.has(tgtAgentId);
          const eitherSelected = selectedIds.has(srcAgentId) || selectedIds.has(tgtAgentId);

          return (
            <GraphLink
              key={ls.link_name}
              linkState={ls}
              x1={srcPos.x}
              y1={srcPos.y}
              x2={tgtPos.x}
              y2={tgtPos.y}
              isHighlighted={!hasSelection || bothSelected}
              isDimmed={hasSelection && !eitherSelected}
              isSelected={selectedLinkName === ls.link_name}
              onSelect={handleLinkSelect}
            />
          );
        })}

        {/* Layer 4: Satellite node dots (agent-colored, slight lighter hue) */}
        {agentNodes.map((agent) => {
          const sats = visibleSatellites.get(agent.agentId) || [];
          const agentDimmed = hasSelection && !selectedIds.has(agent.agentId);
          const satOpacity = agentDimmed ? 0.2 : 0.85;
          const satColor = lightenColor(agent.color, 0.25);

          return sats.map((nws) => {
            const satPos = allPositions.get(`sat:${nws.containerName}`);
            if (!satPos) return null;

            return (
              <circle
                key={`sat:${nws.containerName}`}
                cx={satPos.x}
                cy={satPos.y}
                r={SAT_DOT_R}
                fill={satColor}
                opacity={satOpacity}
                style={{ transition: 'opacity 300ms ease', cursor: 'default' }}
                onPointerEnter={() => setHoverTarget({ type: 'satellite', id: nws.containerName, x: satPos.x, y: satPos.y })}
                onPointerLeave={() => setHoverTarget(prev => prev?.id === nws.containerName ? null : prev)}
              />
            );
          });
        })}

        {/* Layer 5: Agent hub dots (render above everything) */}
        {agentNodes.map((agent) => {
          const pos = positions.get(agent.agentId);
          if (!pos) return null;

          return (
            <AgentNode
              key={agent.agentId}
              agent={agent}
              x={pos.x}
              y={pos.y}
              isSelected={selectedIds.has(agent.agentId)}
              isDimmed={hasSelection && !selectedIds.has(agent.agentId)}
              onSelect={(multi) => handleAgentSelect(agent.agentId, multi)}
              onDrag={handleDrag}
              onDragEnd={handleDragEnd}
              overflowCount={overflowCounts.get(agent.agentId) || 0}
              onHoverEnter={() => setHoverTarget({ type: 'agent', id: agent.agentId, x: pos.x, y: pos.y })}
              onHoverLeave={() => setHoverTarget(prev => prev?.id === agent.agentId ? null : prev)}
            />
          );
        })}
        {/* Layer 6: Hover tooltip */}
        {hoverTarget && (() => {
          const TIP_W = 200;
          const TIP_X = hoverTarget.x + 12;
          const TIP_Y = hoverTarget.y - 10;

          if (hoverTarget.type === 'satellite') {
            const info = satelliteLookup.get(hoverTarget.id);
            if (!info) return null;
            const { nws, agentName } = info;
            const state = nws.state?.actual_state || 'undeployed';
            const isReady = nws.state?.is_ready;
            const model = nws.node.nodeType === 'device' ? modelLookup.get((nws.node as any).model) : null;
            const vendorIfaces = getNodeVendorInterfaces(nws.containerName);
            const lines = [
              nws.node.name,
              model ? `${model.name} (${model.vendor})` : null,
              `State: ${state}${isReady ? ' (ready)' : ''}`,
              `Host: ${agentName}`,
              vendorIfaces.length > 0 ? `Interfaces: ${vendorIfaces.slice(0, 4).join(', ')}${vendorIfaces.length > 4 ? '...' : ''}` : null,
            ].filter(Boolean) as string[];
            const lineH = 14;
            const tipH = lines.length * lineH + 12;

            return (
              <g style={{ pointerEvents: 'none' }}>
                <rect x={TIP_X} y={TIP_Y} width={TIP_W} height={tipH} rx={4} fill="#1c1917" stroke="#44403c" strokeWidth={0.5} opacity={0.95} />
                {lines.map((line, i) => (
                  <text key={i} x={TIP_X + 8} y={TIP_Y + 14 + i * lineH}
                    fill={i === 0 ? '#e7e5e4' : '#a8a29e'} fontSize={i === 0 ? 11 : 10}
                    fontFamily="'JetBrains Mono', monospace"
                    fontWeight={i === 0 ? 600 : 400}
                  >{line}</text>
                ))}
              </g>
            );
          }

          if (hoverTarget.type === 'agent') {
            const agent = agentNodes.find(a => a.agentId === hoverTarget.id);
            if (!agent) return null;
            const lines = [
              agent.agentName,
              `Nodes: ${agent.stats.nodeCount} (${agent.stats.runningCount} running)`,
              `Links: ${agent.stats.linkCount}`,
              agent.stats.vlanTags.size > 0 ? `VLANs: ${agent.stats.vlanTags.size}` : null,
            ].filter(Boolean) as string[];
            const lineH = 14;
            const tipH = lines.length * lineH + 12;

            return (
              <g style={{ pointerEvents: 'none' }}>
                <rect x={TIP_X} y={TIP_Y} width={TIP_W} height={tipH} rx={4} fill="#1c1917" stroke="#44403c" strokeWidth={0.5} opacity={0.95} />
                {lines.map((line, i) => (
                  <text key={i} x={TIP_X + 8} y={TIP_Y + 14 + i * lineH}
                    fill={i === 0 ? '#e7e5e4' : '#a8a29e'} fontSize={i === 0 ? 11 : 10}
                    fontFamily="'JetBrains Mono', monospace"
                    fontWeight={i === 0 ? 600 : 400}
                  >{line}</text>
                ))}
              </g>
            );
          }

          return null;
        })()}
      </svg>

      {/* Instructions hint overlay */}
      {showHint && (
        <div
          className="absolute top-4 left-1/2 -translate-x-1/2 bg-stone-900/70 backdrop-blur text-stone-400 text-xs rounded-lg px-4 py-2 pointer-events-none transition-opacity duration-500"
          style={{ opacity: showHint ? 0.9 : 0 }}
        >
          Click agent to inspect &middot; Shift+click to compare tunnels &middot; Scroll to zoom &middot; Drag to pan
        </div>
      )}

      {/* Navigator controls — bottom-right corner */}
      <div className="absolute bottom-4 right-4 flex flex-col gap-1.5">
        <div className="bg-stone-900/80 backdrop-blur-md border border-stone-700/50 rounded-lg flex flex-col overflow-hidden shadow-lg">
          <button
            title="Zoom in"
            onClick={handleZoomIn}
            className="p-2.5 text-stone-400 hover:text-white hover:bg-stone-800 transition-colors border-b border-stone-700/50"
          >
            <i className="fa-solid fa-plus text-xs" />
          </button>
          <div className="px-2.5 py-1 text-center text-[10px] font-mono text-stone-500 border-b border-stone-700/50 select-none">
            {zoomPercent}%
          </div>
          <button
            title="Zoom out"
            onClick={handleZoomOut}
            className="p-2.5 text-stone-400 hover:text-white hover:bg-stone-800 transition-colors"
          >
            <i className="fa-solid fa-minus text-xs" />
          </button>
        </div>
        <div className="bg-stone-900/80 backdrop-blur-md border border-stone-700/50 rounded-lg flex flex-col overflow-hidden shadow-lg">
          <button
            title="Center view"
            onClick={handleCenter}
            className="p-2.5 text-stone-400 hover:text-white hover:bg-stone-800 transition-colors border-b border-stone-700/50"
          >
            <i className="fa-solid fa-crosshairs text-xs" />
          </button>
          <button
            title="Fit to screen"
            onClick={handleFitToScreen}
            className="p-2.5 text-stone-400 hover:text-white hover:bg-stone-800 transition-colors"
          >
            <i className="fa-solid fa-maximize text-xs" />
          </button>
        </div>
      </div>
    </div>
  );
};

export default AgentGraph;
