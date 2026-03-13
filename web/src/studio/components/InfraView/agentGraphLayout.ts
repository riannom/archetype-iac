import type { AgentGraphNode, NodeWithState } from './types';

export interface Position {
  x: number;
  y: number;
}

export interface AgentPairSummary {
  agentA: string;
  agentB: string;
  count: number;
  hasError: boolean;
  allUp: boolean;
  hasPending: boolean;
}

// Positions are computed in a fixed 800x600 coordinate space
export const WORLD_W = 800;
export const WORLD_H = 600;

const SAT_RADIUS = 55;
export const SAT_DOT_R = 5;
export const MAX_VISIBLE_SATELLITES = 8;

export const MIN_ZOOM = 0.3;
export const MAX_ZOOM = 3;
export const ZOOM_STEP = 0.25;

export const LINK_STATE_COLORS: Record<string, string> = {
  up: '#22c55e',
  error: '#ef4444',
  pending: '#f59e0b',
  down: '#57534e',
  unknown: '#57534e',
};

// Lighten a hex color by mixing toward white
export function lightenColor(hex: string, amount: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const lr = Math.min(255, Math.round(r + (255 - r) * amount));
  const lg = Math.min(255, Math.round(g + (255 - g) * amount));
  const lb = Math.min(255, Math.round(b + (255 - b) * amount));
  return `#${lr.toString(16).padStart(2, '0')}${lg.toString(16).padStart(2, '0')}${lb.toString(16).padStart(2, '0')}`;
}

export function computeInitialPositions(agents: AgentGraphNode[]): Map<string, Position> {
  const positions = new Map<string, Position>();
  const cx = WORLD_W / 2;
  const cy = WORLD_H / 2;

  if (agents.length === 0) return positions;

  if (agents.length === 1) {
    positions.set(agents[0].agentId, { x: cx, y: cy });
    return positions;
  }

  if (agents.length === 2) {
    positions.set(agents[0].agentId, { x: cx - 150, y: cy });
    positions.set(agents[1].agentId, { x: cx + 150, y: cy });
    return positions;
  }

  const radius = Math.min(WORLD_W, WORLD_H) * 0.3;
  const startAngle = -Math.PI / 2;
  agents.forEach((agent, i) => {
    const angle = startAngle + (2 * Math.PI * i) / agents.length;
    positions.set(agent.agentId, {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    });
  });

  return positions;
}

export function computeSatellitePositions(
  agentPos: Position,
  nodes: NodeWithState[],
  crossHostNames: Set<string>,
): Map<string, Position> {
  const positions = new Map<string, Position>();

  // Sort: cross-host-linked first, then alphabetical
  const sorted = [...nodes].sort((a, b) => {
    const aCross = crossHostNames.has(a.containerName) ? 0 : 1;
    const bCross = crossHostNames.has(b.containerName) ? 0 : 1;
    if (aCross !== bCross) return aCross - bCross;
    return a.node.name.localeCompare(b.node.name);
  });

  // Always show cross-host-linked nodes; cap total visible at MAX_VISIBLE_SATELLITES
  const crossHostCount = sorted.filter(n => crossHostNames.has(n.containerName)).length;
  const visibleCount = Math.max(crossHostCount, Math.min(sorted.length, MAX_VISIBLE_SATELLITES));
  const visible = sorted.slice(0, visibleCount);

  if (visible.length === 0) return positions;

  if (visible.length <= 4) {
    // Semicircle above agent (from -150deg to -30deg)
    const startAngle = -Math.PI * 5 / 6;
    const endAngle = -Math.PI / 6;
    const step = visible.length === 1 ? 0 : (endAngle - startAngle) / (visible.length - 1);
    visible.forEach((nws, i) => {
      const angle = visible.length === 1 ? -Math.PI / 2 : startAngle + step * i;
      positions.set(`sat:${nws.containerName}`, {
        x: agentPos.x + SAT_RADIUS * Math.cos(angle),
        y: agentPos.y + SAT_RADIUS * Math.sin(angle),
      });
    });
  } else {
    // Full circle
    const startAngle = -Math.PI / 2;
    visible.forEach((nws, i) => {
      const angle = startAngle + (2 * Math.PI * i) / visible.length;
      positions.set(`sat:${nws.containerName}`, {
        x: agentPos.x + SAT_RADIUS * Math.cos(angle),
        y: agentPos.y + SAT_RADIUS * Math.sin(angle),
      });
    });
  }

  return positions;
}

export function computeFitView(
  positions: Map<string, Position>,
  dims: { width: number; height: number },
): { zoom: number; pan: Position } {
  if (positions.size === 0) return { zoom: 1, pan: { x: 0, y: 0 } };
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  positions.forEach(({ x, y }) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  });
  const pad = 100;
  minX -= pad; minY -= pad; maxX += pad; maxY += pad;
  const contentW = maxX - minX;
  const contentH = maxY - minY;
  const contentCx = (minX + maxX) / 2;
  const contentCy = (minY + maxY) / 2;
  const fitZoom = Math.min(dims.width / contentW, dims.height / contentH, MAX_ZOOM);
  return {
    zoom: Math.max(MIN_ZOOM, fitZoom),
    pan: { x: WORLD_W / 2 - contentCx, y: WORLD_H / 2 - contentCy },
  };
}
