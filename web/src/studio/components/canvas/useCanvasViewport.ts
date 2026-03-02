import { useState, useEffect, useRef, useCallback } from 'react';
import { Node, Annotation, isExternalNetworkNode } from '../../types';

function readStoredViewport(labId?: string): { zoom: number; x: number; y: number } {
  if (!labId) return { zoom: 1, x: 0, y: 0 };
  try {
    const stored = localStorage.getItem(`archetype_canvas_viewport_${labId}`);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (typeof parsed.zoom === 'number' && typeof parsed.x === 'number' && typeof parsed.y === 'number') {
        return parsed;
      }
    }
  } catch { /* ignore */ }
  return { zoom: 1, x: 0, y: 0 };
}

interface UseCanvasViewportArgs {
  labId?: string;
  nodes: Node[];
  annotations: Annotation[];
  containerRef: React.RefObject<HTMLDivElement | null>;
  focusNodeId?: string | null;
  onFocusHandled?: () => void;
}

export function useCanvasViewport({
  labId,
  nodes,
  annotations,
  containerRef,
  focusNodeId,
  onFocusHandled,
}: UseCanvasViewportArgs) {
  const [zoom, setZoom] = useState(() => readStoredViewport(labId).zoom);
  const [offset, setOffset] = useState(() => {
    const vp = readStoredViewport(labId);
    return { x: vp.x, y: vp.y };
  });

  // Track latest viewport for unmount save
  const viewportRef = useRef({ zoom, x: offset.x, y: offset.y });
  useEffect(() => {
    viewportRef.current = { zoom, x: offset.x, y: offset.y };
  }, [zoom, offset]);

  // Save viewport on unmount
  useEffect(() => {
    return () => {
      if (!labId) return;
      try {
        localStorage.setItem(
          `archetype_canvas_viewport_${labId}`,
          JSON.stringify(viewportRef.current)
        );
      } catch { /* ignore */ }
    };
  }, [labId]);

  // Debounced save viewport to localStorage
  useEffect(() => {
    if (!labId) return;
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(
          `archetype_canvas_viewport_${labId}`,
          JSON.stringify({ zoom, x: offset.x, y: offset.y })
        );
      } catch { /* localStorage full or unavailable */ }
    }, 300);
    return () => clearTimeout(timer);
  }, [labId, zoom, offset]);

  // Pan canvas to center a specific node when focusNodeId changes
  useEffect(() => {
    if (!focusNodeId || !containerRef.current) return;
    const node = nodes.find(n => n.id === focusNodeId);
    if (!node) return;
    const rect = containerRef.current.getBoundingClientRect();
    setOffset({
      x: rect.width / 2 - node.x * zoom,
      y: rect.height / 2 - node.y * zoom,
    });
    onFocusHandled?.();
  }, [focusNodeId]);

  const getContentBounds = useCallback(() => {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;

    // Nodes: include icon + name label area (approx) so "fit" keeps labels in view.
    nodes.forEach((n) => {
      const halfW = isExternalNetworkNode(n) ? 28 : 24;
      const halfH = isExternalNetworkNode(n) ? 20 : 24;
      const labelExtraBottom = isExternalNetworkNode(n) ? 34 : 26;

      minX = Math.min(minX, n.x - halfW);
      maxX = Math.max(maxX, n.x + halfW);
      minY = Math.min(minY, n.y - halfH);
      maxY = Math.max(maxY, n.y + halfH + labelExtraBottom);
    });

    // Annotations: rect/circle/text are rendered in the same coordinate space as nodes/links.
    annotations.forEach((ann) => {
      if (ann.type === 'rect') {
        const w = ann.width || 100;
        const h = ann.height || 60;
        minX = Math.min(minX, ann.x);
        minY = Math.min(minY, ann.y);
        maxX = Math.max(maxX, ann.x + w);
        maxY = Math.max(maxY, ann.y + h);
        return;
      }
      if (ann.type === 'circle') {
        const r = ann.width ? ann.width / 2 : 40;
        minX = Math.min(minX, ann.x - r);
        maxX = Math.max(maxX, ann.x + r);
        minY = Math.min(minY, ann.y - r);
        maxY = Math.max(maxY, ann.y + r);
        return;
      }
      if (ann.type === 'text') {
        const text = ann.text || 'New Text';
        const fontSize = ann.fontSize || 14;
        // SVG <text> uses x,y as the baseline start; approximate bounds for fitting.
        const approxW = Math.max(20, text.length * fontSize * 0.6);
        const approxH = Math.max(14, fontSize * 1.2);
        minX = Math.min(minX, ann.x);
        maxX = Math.max(maxX, ann.x + approxW);
        minY = Math.min(minY, ann.y - approxH);
        maxY = Math.max(maxY, ann.y + fontSize * 0.2);
        return;
      }

      if (ann.type === 'arrow') {
        const tx = ann.targetX ?? ann.x + 100;
        const ty = ann.targetY ?? ann.y + 100;
        minX = Math.min(minX, ann.x, tx);
        maxX = Math.max(maxX, ann.x, tx);
        minY = Math.min(minY, ann.y, ty);
        maxY = Math.max(maxY, ann.y, ty);
        return;
      }

      // Unknown annotation type: treat as a small point.
      minX = Math.min(minX, ann.x - 20);
      maxX = Math.max(maxX, ann.x + 20);
      minY = Math.min(minY, ann.y - 20);
      maxY = Math.max(maxY, ann.y + 20);
    });

    if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
      return null;
    }
    return { minX, minY, maxX, maxY };
  }, [nodes, annotations]);

  const applyViewToBounds = useCallback((bounds: { minX: number; minY: number; maxX: number; maxY: number }, newZoom: number) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const contentW = Math.max(1, bounds.maxX - bounds.minX);
    const contentH = Math.max(1, bounds.maxY - bounds.minY);
    setZoom(newZoom);
    setOffset({
      x: (rect.width - contentW * newZoom) / 2 - bounds.minX * newZoom,
      y: (rect.height - contentH * newZoom) / 2 - bounds.minY * newZoom,
    });
  }, [containerRef]);

  // Center the content, and zoom out only if needed to fit everything in view.
  const centerCanvas = useCallback(() => {
    if (!containerRef.current) return;
    const bounds = getContentBounds();
    if (!bounds) {
      setZoom(1);
      setOffset({ x: 0, y: 0 });
      return;
    }

    const rect = containerRef.current.getBoundingClientRect();
    const contentW = Math.max(1, bounds.maxX - bounds.minX);
    const contentH = Math.max(1, bounds.maxY - bounds.minY);

    const zoomToFit = Math.min(rect.width / contentW, rect.height / contentH, 1) * 0.9;
    const fitsAtOne = contentW <= rect.width * 0.9 && contentH <= rect.height * 0.9;
    const nextZoom = fitsAtOne ? 1 : Math.max(0.1, zoomToFit);
    applyViewToBounds(bounds, nextZoom);
  }, [containerRef, getContentBounds, applyViewToBounds]);

  const fitToScreen = useCallback(() => {
    if (!containerRef.current) return;
    const bounds = getContentBounds();
    if (!bounds) return;

    const rect = containerRef.current.getBoundingClientRect();
    const contentW = Math.max(1, bounds.maxX - bounds.minX);
    const contentH = Math.max(1, bounds.maxY - bounds.minY);
    const newZoom = Math.min(rect.width / contentW, rect.height / contentH, 1) * 0.9;
    applyViewToBounds(bounds, Math.max(0.1, newZoom));
  }, [containerRef, getContentBounds, applyViewToBounds]);

  return {
    zoom,
    setZoom,
    offset,
    setOffset,
    centerCanvas,
    fitToScreen,
  };
}
