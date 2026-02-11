import { isExternalNetworkNode, Link, Node } from '../types';

type Endpoint = 'source' | 'target';

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

interface LabelCandidate {
  x: number;
  y: number;
  t: number;
  offset: number;
}

interface LabelRequest {
  key: string;
  linkId: string;
  endpoint: Endpoint;
  text: string;
  from: Point;
  to: Point;
  index: number;
  count: number;
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

function intersects(a: Rect, b: Rect): boolean {
  return !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
}

function overlapArea(a: Rect, b: Rect): number {
  if (!intersects(a, b)) return 0;
  const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  return width * height;
}

function pointDistanceToSegment(point: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) {
    const ddx = point.x - a.x;
    const ddy = point.y - a.y;
    return Math.sqrt(ddx * ddx + ddy * ddy);
  }
  const t = clamp(((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSq, 0, 1);
  const projX = a.x + t * dx;
  const projY = a.y + t * dy;
  const ddx = point.x - projX;
  const ddy = point.y - projY;
  return Math.sqrt(ddx * ddx + ddy * ddy);
}

function buildEndpointIndices(links: Link[]): {
  sourceIndices: Map<string, Map<string, number>>;
  targetIndices: Map<string, Map<string, number>>;
  sourceCounts: Map<string, number>;
  targetCounts: Map<string, number>;
} {
  const sourceIndices = new Map<string, Map<string, number>>();
  const targetIndices = new Map<string, Map<string, number>>();
  const sourceCounts = new Map<string, number>();
  const targetCounts = new Map<string, number>();

  links.forEach((link) => {
    if (!sourceIndices.has(link.source)) {
      sourceIndices.set(link.source, new Map());
      sourceCounts.set(link.source, 0);
    }
    const sourceIndex = sourceCounts.get(link.source) ?? 0;
    sourceIndices.get(link.source)!.set(link.id, sourceIndex);
    sourceCounts.set(link.source, sourceIndex + 1);

    if (!targetIndices.has(link.target)) {
      targetIndices.set(link.target, new Map());
      targetCounts.set(link.target, 0);
    }
    const targetIndex = targetCounts.get(link.target) ?? 0;
    targetIndices.get(link.target)!.set(link.id, targetIndex);
    targetCounts.set(link.target, targetIndex + 1);
  });

  return { sourceIndices, targetIndices, sourceCounts, targetCounts };
}

function buildCandidates(request: LabelRequest): LabelCandidate[] {
  const dx = request.to.x - request.from.x;
  const dy = request.to.y - request.from.y;
  const length = Math.sqrt(dx * dx + dy * dy);
  if (length <= 0.01) return [];

  const unitX = dx / length;
  const unitY = dy / length;
  const perpX = -unitY;
  const perpY = unitX;

  const baseDistance = 58;
  const baseT = clamp(baseDistance / length, 0.16, 0.42);
  const centeredIndex = request.index - (request.count - 1) / 2;
  const baseStagger = centeredIndex * 10;
  const offsetSteps = [
    baseStagger,
    baseStagger + 12,
    baseStagger - 12,
    baseStagger + 24,
    baseStagger - 24,
    baseStagger + 36,
    baseStagger - 36,
    baseStagger + 48,
    baseStagger - 48,
    baseStagger + 64,
    baseStagger - 64,
    0,
  ];
  const tSteps = [
    baseT,
    clamp(baseT + 0.06, 0.12, 0.88),
    clamp(baseT - 0.06, 0.12, 0.88),
    clamp(baseT + 0.12, 0.12, 0.88),
    clamp(baseT - 0.12, 0.12, 0.88),
    clamp(baseT + 0.18, 0.12, 0.88),
  ];

  const candidates: LabelCandidate[] = [];
  tSteps.forEach((t) => {
    const lineX = request.from.x + unitX * length * t;
    const lineY = request.from.y + unitY * length * t;
    offsetSteps.forEach((offset) => {
      candidates.push({
        x: lineX + perpX * offset,
        y: lineY + perpY * offset,
        t,
        offset,
      });
    });
  });
  return candidates;
}

function scoreCandidate(
  candidate: LabelCandidate,
  request: LabelRequest,
  placedRects: Rect[],
  nodeRects: Rect[],
  segmentA: Point,
  segmentB: Point
): number {
  let score = 0;
  const rect = estimateLabelRect(candidate.x, candidate.y, request.text);

  nodeRects.forEach((nodeRect) => {
    const area = overlapArea(rect, nodeRect);
    if (area > 0) score += 10000 + area * 12;
  });

  placedRects.forEach((placedRect) => {
    const area = overlapArea(rect, placedRect);
    if (area > 0) score += 8500 + area * 10;
  });

  const edgeOverflow =
    Math.max(0, -rect.left) +
    Math.max(0, -rect.top) +
    Math.max(0, rect.right - CANVAS_SIZE) +
    Math.max(0, rect.bottom - CANVAS_SIZE);
  score += edgeOverflow * 60;

  const centeredIndex = request.index - (request.count - 1) / 2;
  const preferredOffset = centeredIndex * 10;
  score += Math.abs(candidate.offset - preferredOffset) * 1.5;

  const distanceToLink = pointDistanceToSegment({ x: candidate.x, y: candidate.y }, segmentA, segmentB);
  score += distanceToLink * 3;

  return score;
}

export function computeLinkLabelPlacements(nodes: Node[], links: Link[]): Map<string, LinkLabelPlacement> {
  const nodeMap = new Map<string, Node>();
  nodes.forEach((node) => nodeMap.set(node.id, node));
  const nodeRects = nodes.map(getNodeRect);
  const placements = new Map<string, LinkLabelPlacement>();
  const placedRects: Rect[] = [];

  const { sourceIndices, targetIndices, sourceCounts, targetCounts } = buildEndpointIndices(links);
  const requests: LabelRequest[] = [];

  links.forEach((link) => {
    const source = nodeMap.get(link.source);
    const target = nodeMap.get(link.target);
    if (!source || !target) return;

    if (link.sourceInterface) {
      requests.push({
        key: `${link.id}:source`,
        linkId: link.id,
        endpoint: 'source',
        text: link.sourceInterface,
        from: { x: source.x, y: source.y },
        to: { x: target.x, y: target.y },
        index: sourceIndices.get(link.source)?.get(link.id) ?? 0,
        count: sourceCounts.get(link.source) ?? 1,
      });
    }

    if (link.targetInterface) {
      requests.push({
        key: `${link.id}:target`,
        linkId: link.id,
        endpoint: 'target',
        text: link.targetInterface,
        from: { x: target.x, y: target.y },
        to: { x: source.x, y: source.y },
        index: targetIndices.get(link.target)?.get(link.id) ?? 0,
        count: targetCounts.get(link.target) ?? 1,
      });
    }
  });

  requests.sort((a, b) => {
    if (b.count !== a.count) return b.count - a.count;
    if (b.text.length !== a.text.length) return b.text.length - a.text.length;
    return a.key.localeCompare(b.key);
  });

  requests.forEach((request) => {
    const segmentA = request.from;
    const segmentB = request.to;
    const candidates = buildCandidates(request);
    if (candidates.length === 0) return;

    let best = candidates[0];
    let bestScore = Number.POSITIVE_INFINITY;

    candidates.forEach((candidate) => {
      const score = scoreCandidate(candidate, request, placedRects, nodeRects, segmentA, segmentB);
      if (score < bestScore) {
        best = candidate;
        bestScore = score;
      }
    });

    const rect = estimateLabelRect(best.x, best.y, request.text);
    placedRects.push(rect);

    const current = placements.get(request.linkId) ?? {};
    current[request.endpoint] = {
      x: clamp(best.x, 12, CANVAS_SIZE - 12),
      y: clamp(best.y, 12, CANVAS_SIZE - 12),
    };
    placements.set(request.linkId, current);
  });

  return placements;
}
