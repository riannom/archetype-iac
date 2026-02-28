import { isExternalNetworkNode, Link, Node } from '../types';

interface Point {
  x: number;
  y: number;
}

interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface EndpointPlacement {
  x: number;
  y: number;
}

interface LinkLabelPlacement {
  source?: EndpointPlacement;
  target?: EndpointPlacement;
}

const CANVAS_SIZE = 5000;
const LABEL_HEIGHT = 16;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function estimateLabelRect(x: number, y: number, text: string): Rect {
  const width = Math.max(24, text.length * 7 + 12);
  const halfWidth = width / 2;
  const halfHeight = LABEL_HEIGHT / 2;
  return {
    left: x - halfWidth,
    top: y - halfHeight,
    right: x + halfWidth,
    bottom: y + halfHeight,
  };
}

function getNodeRect(node: Node): Rect {
  if (isExternalNetworkNode(node)) {
    return {
      left: node.x - 28,
      top: node.y - 20,
      right: node.x + 28,
      bottom: node.y + 20,
    };
  }
  return {
    left: node.x - 24,
    top: node.y - 24,
    right: node.x + 24,
    bottom: node.y + 24,
  };
}

function rectsOverlap(a: Rect, b: Rect): boolean {
  return !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
}

/**
 * Place a label directly on the link line near its endpoint.
 * Only nudges perpendicular when the on-line position overlaps a node.
 */
function placeLabel(
  from: Point,
  to: Point,
  text: string,
  nodeRects: Rect[],
  placedRects: Rect[],
): EndpointPlacement {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.sqrt(dx * dx + dy * dy);
  if (length <= 0.01) return { x: from.x, y: from.y + 30 };

  const unitX = dx / length;
  const unitY = dy / length;
  const perpX = -unitY;
  const perpY = unitX;

  // Place at ~55px from endpoint along the link, capped at 35% of link length
  const baseT = clamp(55 / length, 0.15, 0.40);

  // Try positions along the link first (staying ON the line), then with perpendicular nudges
  const tSteps = [
    baseT,
    clamp(baseT + 0.08, 0.10, 0.45),
    clamp(baseT - 0.06, 0.08, 0.45),
    clamp(baseT + 0.16, 0.10, 0.50),
  ];
  const perpNudges = [0, 16, -16, 32, -32];

  for (const t of tSteps) {
    const lineX = from.x + unitX * length * t;
    const lineY = from.y + unitY * length * t;

    for (const nudge of perpNudges) {
      const x = lineX + perpX * nudge;
      const y = lineY + perpY * nudge;
      const rect = estimateLabelRect(x, y, text);

      let overlaps = false;
      for (const nodeRect of nodeRects) {
        if (rectsOverlap(rect, nodeRect)) { overlaps = true; break; }
      }
      if (!overlaps) {
        for (const placed of placedRects) {
          if (rectsOverlap(rect, placed)) { overlaps = true; break; }
        }
      }

      if (!overlaps) {
        placedRects.push(rect);
        return { x: clamp(x, 12, CANVAS_SIZE - 12), y: clamp(y, 12, CANVAS_SIZE - 12) };
      }
    }
  }

  // Fallback: on the line at base position — never flee far from the endpoint
  const x = from.x + unitX * length * baseT;
  const y = from.y + unitY * length * baseT;
  const rect = estimateLabelRect(x, y, text);
  placedRects.push(rect);
  return { x: clamp(x, 12, CANVAS_SIZE - 12), y: clamp(y, 12, CANVAS_SIZE - 12) };
}

export function computeLinkLabelPlacements(nodes: Node[], links: Link[]): Map<string, LinkLabelPlacement> {
  const nodeMap = new Map<string, Node>();
  nodes.forEach((node) => nodeMap.set(node.id, node));
  const nodeRects = nodes.map(getNodeRect);
  const placements = new Map<string, LinkLabelPlacement>();
  const placedRects: Rect[] = [];

  links.forEach((link) => {
    const source = nodeMap.get(link.source);
    const target = nodeMap.get(link.target);
    if (!source || !target) return;

    const placement: LinkLabelPlacement = {};

    if (link.sourceInterface) {
      placement.source = placeLabel(
        { x: source.x, y: source.y },
        { x: target.x, y: target.y },
        link.sourceInterface,
        nodeRects,
        placedRects,
      );
    }

    if (link.targetInterface) {
      placement.target = placeLabel(
        { x: target.x, y: target.y },
        { x: source.x, y: source.y },
        link.targetInterface,
        nodeRects,
        placedRects,
      );
    }

    placements.set(link.id, placement);
  });

  return placements;
}
