
import React, { useRef, useState, useEffect, useCallback, useMemo, memo } from 'react';
import { Node, Link, DeviceType, Annotation, AnnotationType, CanvasTool, DeviceModel, isExternalNetworkNode, isDeviceNode } from '../types';
import { RuntimeStatus } from './RuntimeControl';
import { useTheme } from '../../theme/index';
import { getAgentColor, getAgentInitials } from '../../utils/agentColors';
import { useNotifications } from '../../contexts/NotificationContext';
import { NodeStateEntry } from '../../types/nodeState';
import { LinkStateData } from '../hooks/useLabStateWS';
import { computeLinkLabelPlacements } from '../utils/linkLabelPlacement';

interface CanvasProps {
  nodes: Node[];
  links: Link[];
  annotations: Annotation[];
  runtimeStates: Record<string, RuntimeStatus>;
  nodeStates?: Record<string, NodeStateEntry>;
  linkStates?: Map<string, LinkStateData>;
  scenarioHighlights?: { activeNodeNames: Set<string>; activeLinkName: string | null; stepName: string };
  deviceModels: DeviceModel[];
  labId?: string;
  agents?: { id: string; name: string }[];
  showAgentIndicators?: boolean;
  onToggleAgentIndicators?: () => void;
  activeTool?: CanvasTool;
  onToolCreate?: (type: AnnotationType, x: number, y: number, opts?: { width?: number; height?: number; targetX?: number; targetY?: number }) => void;
  onNodeMove: (id: string, x: number, y: number) => void;
  onAnnotationMove: (id: string, x: number, y: number) => void;
  onConnect: (sourceId: string, targetId: string) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onOpenConsole: (nodeId: string) => void;
  onExtractConfig?: (nodeId: string) => void;
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => void;
  onDelete: (id: string) => void;
  onDropDevice?: (model: DeviceModel, x: number, y: number) => void;
  onDropExternalNetwork?: (x: number, y: number) => void;
  onUpdateAnnotation?: (id: string, updates: Partial<Annotation>) => void;
  selectedIds?: Set<string>;
  onSelectMultiple?: (ids: Set<string>) => void;
}

type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w';

interface ResizeState {
  id: string;
  handle: ResizeHandle;
  startX: number;
  startY: number;
  startWidth: number;
  startHeight: number;
  startAnnX: number;
  startAnnY: number;
}

interface ContextMenu {
  x: number;
  y: number;
  id: string;
  type: 'node' | 'link';
}

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

const Canvas: React.FC<CanvasProps> = ({
  nodes, links, annotations, runtimeStates, nodeStates = {}, linkStates, scenarioHighlights, deviceModels, labId, agents = [], showAgentIndicators = false, onToggleAgentIndicators, activeTool = 'pointer', onToolCreate, onNodeMove, onAnnotationMove, onConnect, selectedId, onSelect, onOpenConsole, onExtractConfig, onUpdateStatus, onDelete, onDropDevice, onDropExternalNetwork, onUpdateAnnotation, selectedIds, onSelectMultiple
}) => {
  const { effectiveMode } = useTheme();
  const { preferences } = useNotifications();
  const errorIndicatorSettings = preferences?.canvas_settings.errorIndicator;
  const containerRef = useRef<HTMLDivElement>(null);
  const [draggingNode, setDraggingNode] = useState<string | null>(null);
  const [draggingAnnotation, setDraggingAnnotation] = useState<string | null>(null);
  const [linkingNode, setLinkingNode] = useState<string | null>(null);
  const [hoveredLinkId, setHoveredLinkId] = useState<string | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null);

  const [zoom, setZoom] = useState(() => readStoredViewport(labId).zoom);
  const [offset, setOffset] = useState(() => {
    const vp = readStoredViewport(labId);
    return { x: vp.x, y: vp.y };
  });
  const [isPanning, setIsPanning] = useState(false);
  const [resizing, setResizing] = useState<ResizeState | null>(null);
  const [drawStart, setDrawStart] = useState<{ x: number; y: number } | null>(null);
  const [drawEnd, setDrawEnd] = useState<{ x: number; y: number } | null>(null);
  const drawStartRef = useRef<{ x: number; y: number } | null>(null);
  const panStartRef = useRef<{ x: number; y: number } | null>(null);
  const didPanRef = useRef(false);
  const [editingText, setEditingText] = useState<{ id: string; x: number; y: number } | null>(null);
  const pendingTextEditRef = useRef(false);
  const textEditCommittedRef = useRef(false);
  const textInputRef = useRef<HTMLInputElement>(null);
  const [marqueeStart, setMarqueeStart] = useState<{ x: number; y: number } | null>(null);
  const [marqueeEnd, setMarqueeEnd] = useState<{ x: number; y: number } | null>(null);
  const marqueeRef = useRef<{ x: number; y: number } | null>(null);

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

  // Elapsed timer: re-render every second when any node is in a transitional state
  const hasTransitionalNodes = useMemo(() => {
    return Object.values(nodeStates).some(ns => {
      const ds = ns.display_state;
      return ds === 'starting' || ds === 'stopping';
    });
  }, [nodeStates]);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!hasTransitionalNodes) return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [hasTransitionalNodes]);

  // Memoized node map for O(1) lookups instead of O(n) .find() calls
  const nodeMap = useMemo(() => {
    const map = new Map<string, Node>();
    nodes.forEach(node => map.set(node.id, node));
    return map;
  }, [nodes]);

  const linkLabelPlacements = useMemo(() => computeLinkLabelPlacements(nodes, links), [nodes, links]);

  // Build set of highlighted node names from scenario execution
  const highlightedNodeNames = scenarioHighlights?.activeNodeNames;

  // Parse scenario activeLinkName into node name pair for matching
  const highlightedLinkNodes = useMemo(() => {
    if (!scenarioHighlights?.activeLinkName) return null;
    const parts = scenarioHighlights.activeLinkName.split(' <-> ');
    if (parts.length !== 2) return null;
    return {
      a: parts[0].trim().split(':')[0],
      b: parts[1].trim().split(':')[0],
    };
  }, [scenarioHighlights?.activeLinkName]);

  // Build a lookup map from node name pairs to link actual_state for state-based coloring
  const linkActualStateMap = useMemo(() => {
    const map = new Map<string, string>();
    if (!linkStates) return map;
    linkStates.forEach((ls) => {
      // Key by both orderings so either direction matches
      map.set(`${ls.source_node}|${ls.target_node}`, ls.actual_state);
      map.set(`${ls.target_node}|${ls.source_node}`, ls.actual_state);
    });
    return map;
  }, [linkStates]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (editingText) return; // Don't delete while editing text
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const target = e.target as HTMLElement;
        if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA') {
          if (selectedIds && selectedIds.size > 0) {
            selectedIds.forEach(id => onDelete(id));
          } else if (selectedId) {
            onDelete(selectedId);
          }
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedId, selectedIds, onDelete, editingText]);

  // Enter inline edit mode when a text annotation is just created
  useEffect(() => {
    if (!pendingTextEditRef.current || !selectedId) return;
    const ann = annotations.find(a => a.id === selectedId && a.type === 'text');
    if (ann) {
      pendingTextEditRef.current = false;
      textEditCommittedRef.current = false;
      setEditingText({ id: ann.id, x: ann.x, y: ann.y });
    }
  }, [selectedId, annotations]);

  useEffect(() => {
    const handleClickOutside = () => setContextMenu(null);
    window.addEventListener('click', handleClickOutside);
    return () => window.removeEventListener('click', handleClickOutside);
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left - offset.x) / zoom;
    const y = (e.clientY - rect.top - offset.y) / zoom;
    setMousePos({ x, y });

    if (isPanning) {
      setOffset(prev => ({ x: prev.x + e.movementX, y: prev.y + e.movementY }));
      if (!didPanRef.current && panStartRef.current) {
        const dx = e.clientX - panStartRef.current.x;
        const dy = e.clientY - panStartRef.current.y;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
          didPanRef.current = true;
        }
      }
      return;
    }

    // Marquee selection tracking (pointer tool, empty canvas drag)
    if (marqueeRef.current) {
      if (!didPanRef.current && panStartRef.current) {
        const dx = e.clientX - panStartRef.current.x;
        const dy = e.clientY - panStartRef.current.y;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
          didPanRef.current = true;
        }
      }
      if (didPanRef.current) {
        setMarqueeEnd({ x, y });
      }
      return;
    }

    if (resizing && onUpdateAnnotation) {
      const dx = x - resizing.startX;
      const dy = y - resizing.startY;
      const ann = annotations.find(a => a.id === resizing.id);
      if (!ann) return;

      let newWidth = resizing.startWidth;
      let newHeight = resizing.startHeight;
      let newX = resizing.startAnnX;
      let newY = resizing.startAnnY;

      if (ann.type === 'arrow') {
        // Arrow: 'n' handle moves start, 's' handle moves end
        if (resizing.handle === 'n') {
          onUpdateAnnotation(resizing.id, { x, y });
        } else {
          onUpdateAnnotation(resizing.id, { targetX: x, targetY: y });
        }
        return;
      } else if (ann.type === 'circle') {
        // For circles, resize uniformly based on drag distance
        const delta = Math.max(Math.abs(dx), Math.abs(dy));
        const sign = (resizing.handle.includes('e') || resizing.handle.includes('s')) ? 1 : -1;
        newWidth = Math.max(20, resizing.startWidth + delta * sign * 2);
      } else if (ann.type === 'rect') {
        // For rects, resize based on which handle is being dragged
        const handle = resizing.handle;

        if (handle.includes('e')) {
          newWidth = Math.max(20, resizing.startWidth + dx);
        }
        if (handle.includes('w')) {
          newWidth = Math.max(20, resizing.startWidth - dx);
          newX = resizing.startAnnX + dx;
        }
        if (handle.includes('s')) {
          newHeight = Math.max(20, resizing.startHeight + dy);
        }
        if (handle.includes('n')) {
          newHeight = Math.max(20, resizing.startHeight - dy);
          newY = resizing.startAnnY + dy;
        }
      }

      onUpdateAnnotation(resizing.id, { width: newWidth, height: newHeight, x: newX, y: newY });
      return;
    }

    if (drawStartRef.current && activeTool !== 'pointer' && activeTool !== 'text') {
      setDrawEnd({ x, y });
      return;
    }

    if (draggingNode) {
      onNodeMove(draggingNode, x, y);
    } else if (draggingAnnotation) {
      onAnnotationMove(draggingAnnotation, x, y);
    }
  }, [offset, zoom, isPanning, draggingNode, draggingAnnotation, resizing, annotations, activeTool, onNodeMove, onAnnotationMove, onUpdateAnnotation]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const factor = Math.pow(1.1, -e.deltaY / 100);
      const newZoom = Math.min(Math.max(0.1, zoom * factor), 5);
      const rect = containerRef.current!.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;
      const newOffsetX = mouseX - (mouseX - offset.x) * (newZoom / zoom);
      const newOffsetY = mouseY - (mouseY - offset.y) * (newZoom / zoom);
      setZoom(newZoom);
      setOffset({ x: newOffsetX, y: newOffsetY });
    } else {
      setOffset(prev => ({ x: prev.x - e.deltaX, y: prev.y - e.deltaY }));
    }
  }, [zoom, offset]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    setContextMenu(null);
    // Middle-button pan always works regardless of tool
    if (e.button === 1) {
      setIsPanning(true);
      panStartRef.current = { x: e.clientX, y: e.clientY };
      didPanRef.current = false;
      return;
    }
    if (e.button === 0) {
      // Tool mode: left-click starts tool gesture
      if (activeTool !== 'pointer' && onToolCreate && containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        const x = (e.clientX - rect.left - offset.x) / zoom;
        const y = (e.clientY - rect.top - offset.y) / zoom;

        if (activeTool === 'text') {
          // Text: single click places and enters edit mode
          pendingTextEditRef.current = true;
          onToolCreate('text', x, y);
          return;
        }
        // Rect/circle/arrow: start drag gesture
        drawStartRef.current = { x, y };
        setDrawStart({ x, y });
        setDrawEnd({ x, y });
        e.preventDefault();
        return;
      }
      // Pointer: start marquee tracking (pan via middle-click or scroll)
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        const cx = (e.clientX - rect.left - offset.x) / zoom;
        const cy = (e.clientY - rect.top - offset.y) / zoom;
        marqueeRef.current = { x: cx, y: cy };
        setMarqueeStart({ x: cx, y: cy });
        setMarqueeEnd({ x: cx, y: cy });
      }
      panStartRef.current = { x: e.clientX, y: e.clientY };
      didPanRef.current = false;
      return;
    }
  }, [activeTool, onToolCreate, offset, zoom]);

  const handleMouseUp = useCallback(() => {
    // Complete draw gesture for tool mode
    const start = drawStartRef.current;
    if (start && drawEnd && activeTool !== 'pointer' && activeTool !== 'text' && onToolCreate) {
      const dx = drawEnd.x - start.x;
      const dy = drawEnd.y - start.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist >= 10) {
        if (activeTool === 'rect') {
          const x = Math.min(start.x, drawEnd.x);
          const y = Math.min(start.y, drawEnd.y);
          onToolCreate('rect', x, y, { width: Math.abs(dx), height: Math.abs(dy) });
        } else if (activeTool === 'circle') {
          const diameter = dist * 2;
          onToolCreate('circle', start.x, start.y, { width: diameter });
        } else if (activeTool === 'arrow') {
          onToolCreate('arrow', start.x, start.y, { targetX: drawEnd.x, targetY: drawEnd.y });
        }
      }
      drawStartRef.current = null;
      setDrawStart(null);
      setDrawEnd(null);
      return;
    }
    drawStartRef.current = null;
    setDrawStart(null);
    setDrawEnd(null);

    // Marquee selection completion
    if (marqueeRef.current) {
      const mStart = marqueeRef.current;
      const mEnd = marqueeEnd;
      marqueeRef.current = null;
      setMarqueeStart(null);
      setMarqueeEnd(null);
      panStartRef.current = null;

      if (didPanRef.current && mEnd) {
        const dx = mEnd.x - mStart.x;
        const dy = mEnd.y - mStart.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist >= 10) {
          const left = Math.min(mStart.x, mEnd.x);
          const right = Math.max(mStart.x, mEnd.x);
          const mTop = Math.min(mStart.y, mEnd.y);
          const mBottom = Math.max(mStart.y, mEnd.y);
          const ids = new Set<string>();

          nodes.forEach(n => {
            if (n.x >= left && n.x <= right && n.y >= mTop && n.y <= mBottom) {
              ids.add(n.id);
            }
          });

          annotations.forEach(ann => {
            if (ann.type === 'rect') {
              const w = ann.width || 100;
              const h = ann.height || 60;
              if (ann.x + w >= left && ann.x <= right && ann.y + h >= mTop && ann.y <= mBottom) {
                ids.add(ann.id);
              }
            } else if (ann.type === 'circle') {
              const r = ann.width ? ann.width / 2 : 40;
              if (ann.x + r >= left && ann.x - r <= right && ann.y + r >= mTop && ann.y - r <= mBottom) {
                ids.add(ann.id);
              }
            } else if (ann.type === 'text') {
              const text = ann.text || 'New Text';
              const fontSize = ann.fontSize || 14;
              const approxW = Math.max(20, text.length * fontSize * 0.6);
              const approxH = fontSize * 1.2;
              if (ann.x + approxW >= left && ann.x <= right && ann.y >= mTop && ann.y - approxH <= mBottom) {
                ids.add(ann.id);
              }
            } else if (ann.type === 'arrow') {
              const tx = ann.targetX ?? ann.x + 100;
              const ty = ann.targetY ?? ann.y + 100;
              if ((ann.x >= left && ann.x <= right && ann.y >= mTop && ann.y <= mBottom) ||
                  (tx >= left && tx <= right && ty >= mTop && ty <= mBottom)) {
                ids.add(ann.id);
              }
            }
          });

          if (ids.size > 0 && onSelectMultiple) {
            onSelectMultiple(ids);
          } else {
            onSelect(null);
          }
          setDraggingNode(null);
          setDraggingAnnotation(null);
          setIsPanning(false);
          setLinkingNode(null);
          setResizing(null);
          return;
        }
      }
      // Small movement or click — deselect
      onSelect(null);
      setDraggingNode(null);
      setDraggingAnnotation(null);
      setIsPanning(false);
      setLinkingNode(null);
      setResizing(null);
      return;
    }

    if (isPanning && !didPanRef.current) {
      onSelect(null);
    }
    setDraggingNode(null);
    setDraggingAnnotation(null);
    setIsPanning(false);
    panStartRef.current = null;
    setLinkingNode(null);
    setResizing(null);
  }, [isPanning, onSelect, drawEnd, activeTool, onToolCreate, marqueeEnd, onSelectMultiple, nodes, annotations]);

  const handleNodeMouseDown = (e: React.MouseEvent, id: string) => {
    if (e.button === 2) return;
    e.stopPropagation();
    setContextMenu(null);
    if (e.shiftKey) {
      setLinkingNode(id);
    } else {
      setDraggingNode(id);
      onSelect(id);
    }
  };

  const handleAnnotationMouseDown = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    setDraggingAnnotation(id);
    onSelect(id);
  };

  const handleResizeMouseDown = (e: React.MouseEvent, ann: Annotation, handle: ResizeHandle) => {
    e.stopPropagation();
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left - offset.x) / zoom;
    const y = (e.clientY - rect.top - offset.y) / zoom;
    setResizing({
      id: ann.id,
      handle,
      startX: x,
      startY: y,
      startWidth: ann.width || (ann.type === 'rect' ? 100 : 80),
      startHeight: ann.height || 60,
      startAnnX: ann.x,
      startAnnY: ann.y,
    });
  };

  const getResizeCursor = (handle: ResizeHandle): string => {
    const cursors: Record<ResizeHandle, string> = {
      'nw': 'nwse-resize',
      'n': 'ns-resize',
      'ne': 'nesw-resize',
      'e': 'ew-resize',
      'se': 'nwse-resize',
      's': 'ns-resize',
      'sw': 'nesw-resize',
      'w': 'ew-resize',
    };
    return cursors[handle];
  };

  const handleNodeContextMenu = (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    onSelect(id);
    setContextMenu({ x: e.clientX, y: e.clientY, id, type: 'node' });
  };

  const handleLinkContextMenu = (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    onSelect(id);
    setContextMenu({ x: e.clientX, y: e.clientY, id, type: 'link' });
  };

  const handleNodeMouseUp = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (linkingNode && linkingNode !== id) {
      onConnect(linkingNode, id);
    }
    setLinkingNode(null);
    setDraggingNode(null);
  };

  const handleLinkMouseDown = (e: React.MouseEvent, id: string) => {
    if (e.button === 2) return;
    e.stopPropagation();
    setContextMenu(null);
    onSelect(id);
  };

  const getContentBounds = () => {
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
  };

  const applyViewToBounds = (bounds: { minX: number; minY: number; maxX: number; maxY: number }, newZoom: number) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const contentW = Math.max(1, bounds.maxX - bounds.minX);
    const contentH = Math.max(1, bounds.maxY - bounds.minY);
    setZoom(newZoom);
    setOffset({
      x: (rect.width - contentW * newZoom) / 2 - bounds.minX * newZoom,
      y: (rect.height - contentH * newZoom) / 2 - bounds.minY * newZoom,
    });
  };

  // Center the content, and zoom out only if needed to fit everything in view.
  const centerCanvas = () => {
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
  };

  const fitToScreen = () => {
    if (!containerRef.current) return;
    const bounds = getContentBounds();
    if (!bounds) return;

    const rect = containerRef.current.getBoundingClientRect();
    const contentW = Math.max(1, bounds.maxX - bounds.minX);
    const contentH = Math.max(1, bounds.maxY - bounds.minY);
    const newZoom = Math.min(rect.width / contentW, rect.height / contentH, 1) * 0.9;
    applyViewToBounds(bounds, Math.max(0.1, newZoom));
  };

  const getNodeIcon = (modelId: string) => deviceModels.find(m => m.id === modelId)?.icon || 'fa-arrows-to-dot';

  const handleAction = (action: string) => {
    if (contextMenu) {
      switch (action) {
        case 'delete': onDelete(contextMenu.id); break;
        case 'console': onOpenConsole(contextMenu.id); break;
        case 'extract-config': onExtractConfig?.(contextMenu.id); break;
        case 'start': onUpdateStatus(contextMenu.id, 'booting'); break;
        case 'stop': onUpdateStatus(contextMenu.id, 'stopped'); break;
        case 'reload': onUpdateStatus(contextMenu.id, 'booting'); break;
      }
      setContextMenu(null);
    }
  };

  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('application/x-archetype-device') ||
        e.dataTransfer.types.includes('application/x-archetype-external')) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left - offset.x) / zoom;
    const y = (e.clientY - rect.top - offset.y) / zoom;

    const deviceData = e.dataTransfer.getData('application/x-archetype-device');
    if (deviceData && onDropDevice) {
      try {
        const model = JSON.parse(deviceData) as DeviceModel;
        onDropDevice(model, x, y);
      } catch { /* ignore parse errors */ }
      return;
    }

    const externalData = e.dataTransfer.getData('application/x-archetype-external');
    if (externalData && onDropExternalNetwork) {
      onDropExternalNetwork(x, y);
    }
  }, [offset, zoom, onDropDevice, onDropExternalNetwork]);

  return (
    <div
      ref={containerRef}
      className={`flex-1 relative overflow-hidden canvas-grid ${
        effectiveMode === 'dark' ? 'bg-stone-950' : 'bg-stone-50'
      } ${isPanning ? 'cursor-grabbing' : activeTool === 'text' ? 'cursor-text' : activeTool !== 'pointer' ? 'cursor-crosshair' : 'cursor-default'}`}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseDown={handleMouseDown}
      onWheel={handleWheel}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div
        className="absolute inset-0 origin-top-left"
        style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})` }}
      >
        <svg className="absolute inset-0 w-[5000px] h-[5000px] pointer-events-none" style={{ overflow: 'visible' }}>
          {[...annotations].sort((a, b) => (a.zIndex ?? 0) - (b.zIndex ?? 0)).map(ann => {
            const isSelected = selectedId === ann.id || selectedIds?.has(ann.id) === true;
            const stroke = isSelected ? (effectiveMode === 'dark' ? '#65A30D' : '#4D7C0F') : (ann.color || (effectiveMode === 'dark' ? '#57534E' : '#D6D3D1'));
            const handleSize = 8;
            const handleFill = effectiveMode === 'dark' ? '#65A30D' : '#4D7C0F';

            // Render resize handles for rect
            const renderRectHandles = () => {
              if (!isSelected || ann.type !== 'rect') return null;
              const w = ann.width || 100;
              const h = ann.height || 60;
              const handles: { handle: ResizeHandle; cx: number; cy: number }[] = [
                { handle: 'nw', cx: ann.x, cy: ann.y },
                { handle: 'n', cx: ann.x + w / 2, cy: ann.y },
                { handle: 'ne', cx: ann.x + w, cy: ann.y },
                { handle: 'e', cx: ann.x + w, cy: ann.y + h / 2 },
                { handle: 'se', cx: ann.x + w, cy: ann.y + h },
                { handle: 's', cx: ann.x + w / 2, cy: ann.y + h },
                { handle: 'sw', cx: ann.x, cy: ann.y + h },
                { handle: 'w', cx: ann.x, cy: ann.y + h / 2 },
              ];
              return handles.map(({ handle, cx, cy }) => (
                <rect
                  key={handle}
                  x={cx - handleSize / 2}
                  y={cy - handleSize / 2}
                  width={handleSize}
                  height={handleSize}
                  fill={handleFill}
                  stroke={effectiveMode === 'dark' ? '#1C1917' : 'white'}
                  strokeWidth="1"
                  style={{ cursor: getResizeCursor(handle) }}
                  onMouseDown={(e) => handleResizeMouseDown(e, ann, handle)}
                />
              ));
            };

            // Render resize handles for circle (4 cardinal points)
            const renderCircleHandles = () => {
              if (!isSelected || ann.type !== 'circle') return null;
              const r = ann.width ? ann.width / 2 : 40;
              const handles: { handle: ResizeHandle; cx: number; cy: number }[] = [
                { handle: 'n', cx: ann.x, cy: ann.y - r },
                { handle: 'e', cx: ann.x + r, cy: ann.y },
                { handle: 's', cx: ann.x, cy: ann.y + r },
                { handle: 'w', cx: ann.x - r, cy: ann.y },
              ];
              return handles.map(({ handle, cx, cy }) => (
                <rect
                  key={handle}
                  x={cx - handleSize / 2}
                  y={cy - handleSize / 2}
                  width={handleSize}
                  height={handleSize}
                  fill={handleFill}
                  stroke={effectiveMode === 'dark' ? '#1C1917' : 'white'}
                  strokeWidth="1"
                  style={{ cursor: getResizeCursor(handle) }}
                  onMouseDown={(e) => handleResizeMouseDown(e, ann, handle)}
                />
              ));
            };

            // Render arrow endpoint handles (start = 'n', end = 's')
            const renderArrowHandles = () => {
              if (!isSelected || ann.type !== 'arrow') return null;
              const tx = ann.targetX ?? ann.x + 100;
              const ty = ann.targetY ?? ann.y + 100;
              const endpoints: { handle: ResizeHandle; cx: number; cy: number }[] = [
                { handle: 'n', cx: ann.x, cy: ann.y },
                { handle: 's', cx: tx, cy: ty },
              ];
              return endpoints.map(({ handle, cx, cy }) => (
                <circle
                  key={handle}
                  cx={cx}
                  cy={cy}
                  r={handleSize / 2 + 1}
                  fill={handleFill}
                  stroke={effectiveMode === 'dark' ? '#1C1917' : 'white'}
                  strokeWidth="1"
                  style={{ cursor: 'move' }}
                  onMouseDown={(e) => handleResizeMouseDown(e, ann, handle)}
                />
              ));
            };

            // Render arrow SVG
            const renderArrow = () => {
              if (ann.type !== 'arrow') return null;
              const tx = ann.targetX ?? ann.x + 100;
              const ty = ann.targetY ?? ann.y + 100;
              const dx = tx - ann.x;
              const dy = ty - ann.y;
              const len = Math.sqrt(dx * dx + dy * dy);
              if (len < 1) return null;
              const ux = dx / len;
              const uy = dy / len;
              const headLen = 12;
              const headW = 6;
              // Arrowhead at target end
              const tipX = tx;
              const tipY = ty;
              const baseX = tx - ux * headLen;
              const baseY = ty - uy * headLen;
              const leftX = baseX - uy * headW;
              const leftY = baseY + ux * headW;
              const rightX = baseX + uy * headW;
              const rightY = baseY - ux * headW;
              return (
                <>
                  <line x1={ann.x} y1={ann.y} x2={baseX} y2={baseY} stroke={stroke} strokeWidth="2" strokeDasharray={isSelected ? "4" : "0"} />
                  <polygon points={`${tipX},${tipY} ${leftX},${leftY} ${rightX},${rightY}`} fill={stroke} />
                </>
              );
            };

            const handleTextDoubleClick = (e: React.MouseEvent) => {
              if (ann.type === 'text') {
                e.stopPropagation();
                textEditCommittedRef.current = false;
                setEditingText({ id: ann.id, x: ann.x, y: ann.y });
              }
            };

            return (
              <g key={ann.id} className="pointer-events-auto cursor-move" onMouseDown={(e) => handleAnnotationMouseDown(e, ann.id)} onDoubleClick={handleTextDoubleClick}>
                {ann.type === 'rect' && <rect x={ann.x} y={ann.y} width={ann.width || 100} height={ann.height || 60} fill={effectiveMode === 'dark' ? "rgba(68, 64, 60, 0.2)" : "rgba(214, 211, 209, 0.2)"} stroke={stroke} strokeWidth="2" strokeDasharray={isSelected ? "4" : "0"} rx="4" />}
                {ann.type === 'circle' && <circle cx={ann.x} cy={ann.y} r={ann.width ? ann.width / 2 : 40} fill={effectiveMode === 'dark' ? "rgba(68, 64, 60, 0.2)" : "rgba(214, 211, 209, 0.2)"} stroke={stroke} strokeWidth="2" strokeDasharray={isSelected ? "4" : "0"} />}
                {ann.type === 'text' && !(editingText?.id === ann.id) && (() => {
                  const text = ann.text || 'New Text';
                  const fontSize = ann.fontSize || 14;
                  const pad = 4;
                  const approxW = Math.max(20, text.length * fontSize * 0.6);
                  const approxH = fontSize * 1.2;
                  return (
                    <>
                      {isSelected && <rect x={ann.x - pad} y={ann.y - approxH - pad} width={approxW + pad * 2} height={approxH + pad * 2} fill="none" stroke={handleFill} strokeWidth="1.5" strokeDasharray="4" rx="3" />}
                      <text x={ann.x} y={ann.y} fill={ann.color || (effectiveMode === 'dark' ? 'white' : '#1C1917')} fontSize={fontSize} className="font-black tracking-tight select-none">{text}</text>
                    </>
                  );
                })()}
                {renderArrow()}
                {renderRectHandles()}
                {renderCircleHandles()}
                {renderArrowHandles()}
              </g>
            );
          })}

          {links.map(link => {
            const source = nodeMap.get(link.source);
            const target = nodeMap.get(link.target);
            if (!source || !target) return null;
            const isSelected = selectedId === link.id;
            const isHovered = hoveredLinkId === link.id;

            // Check if either endpoint is an external network node
            const isExternalLink = isExternalNetworkNode(source) || isExternalNetworkNode(target);

            // Use blue colors for external links, green/gray for regular links
            let linkColor: string;
            if (isExternalLink) {
              linkColor = isSelected
                ? (effectiveMode === 'dark' ? '#3B82F6' : '#2563EB')
                : (isHovered ? (effectiveMode === 'dark' ? '#60A5FA' : '#3B82F6') : (effectiveMode === 'dark' ? '#6366F1' : '#A5B4FC'));
            } else {
              linkColor = isSelected
                ? (effectiveMode === 'dark' ? '#65A30D' : '#4D7C0F')
                : (isHovered ? (effectiveMode === 'dark' ? '#84CC16' : '#65A30D') : (effectiveMode === 'dark' ? '#57534E' : '#D6D3D1'));
            }

            // Override with state-based color when link is not selected/hovered
            if (!isSelected && !isHovered && !isExternalLink) {
              const sourceName = source.name;
              const targetName = target.name;
              const actualState = linkActualStateMap.get(`${sourceName}|${targetName}`);
              if (actualState) {
                const stateColors: Record<string, { dark: string; light: string }> = {
                  up: { dark: '#22c55e', light: '#16a34a' },
                  error: { dark: '#ef4444', light: '#dc2626' },
                  pending: { dark: '#f59e0b', light: '#d97706' },
                  creating: { dark: '#f59e0b', light: '#d97706' },
                };
                const colors = stateColors[actualState];
                if (colors) {
                  linkColor = effectiveMode === 'dark' ? colors.dark : colors.light;
                }
                // down/unknown → keep default stone color (no change)
              }
            }

            const labelPlacement = linkLabelPlacements.get(link.id);
            const sourceLabelX = labelPlacement?.source?.x;
            const sourceLabelY = labelPlacement?.source?.y;
            const targetLabelX = labelPlacement?.target?.x;
            const targetLabelY = labelPlacement?.target?.y;

            // Label styling for better contrast
            const labelColor = effectiveMode === 'dark' ? '#E7E5E4' : '#44403C';
            const labelStroke = effectiveMode === 'dark' ? '#1C1917' : '#FFFFFF';

            // Scenario highlight: check if this link matches the active scenario step
            const isScenarioHighlighted = highlightedLinkNodes && (
              (source.name === highlightedLinkNodes.a && target.name === highlightedLinkNodes.b) ||
              (source.name === highlightedLinkNodes.b && target.name === highlightedLinkNodes.a)
            );

            return (
              <g key={link.id} className="pointer-events-auto cursor-pointer">
                <line x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="transparent" strokeWidth="12" onMouseDown={(e) => handleLinkMouseDown(e, link.id)} onContextMenu={(e) => handleLinkContextMenu(e, link.id)} onMouseEnter={() => setHoveredLinkId(link.id)} onMouseLeave={() => setHoveredLinkId(null)} />
                {isScenarioHighlighted && (
                  <line
                    x1={source.x} y1={source.y} x2={target.x} y2={target.y}
                    stroke={effectiveMode === 'dark' ? '#3B82F6' : '#2563EB'}
                    strokeWidth="6"
                    strokeOpacity="0.4"
                    className="animate-pulse"
                  />
                )}
                <line
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={linkColor}
                  strokeWidth={isSelected || isHovered ? "3" : "2"}
                  strokeDasharray={isExternalLink ? "6 4" : undefined}
                  className={draggingNode ? '' : 'transition-[stroke,stroke-width] duration-150'}
                />
                {/* Port labels */}
                {link.sourceInterface && sourceLabelX !== undefined && sourceLabelY !== undefined && (
                  <text
                    x={sourceLabelX}
                    y={sourceLabelY}
                    fill={labelColor}
                    stroke={labelStroke}
                    strokeWidth="3"
                    paintOrder="stroke"
                    fontSize="11"
                    fontWeight="700"
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="pointer-events-none select-none"
                    style={{ fontFamily: 'ui-monospace, monospace' }}
                  >
                    {link.sourceInterface}
                  </text>
                )}
                {link.targetInterface && targetLabelX !== undefined && targetLabelY !== undefined && (
                  <text
                    x={targetLabelX}
                    y={targetLabelY}
                    fill={labelColor}
                    stroke={labelStroke}
                    strokeWidth="3"
                    paintOrder="stroke"
                    fontSize="11"
                    fontWeight="700"
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="pointer-events-none select-none"
                    style={{ fontFamily: 'ui-monospace, monospace' }}
                  >
                    {link.targetInterface}
                  </text>
                )}
              </g>
            );
          })}

          {linkingNode && (
            <line
              x1={nodeMap.get(linkingNode)?.x}
              y1={nodeMap.get(linkingNode)?.y}
              x2={mousePos.x}
              y2={mousePos.y}
              stroke="#65A30D"
              strokeWidth="2"
              strokeDasharray="4"
            />
          )}

          {/* Draw preview during tool gesture */}
          {drawStart && drawEnd && activeTool === 'rect' && (
            <rect
              x={Math.min(drawStart.x, drawEnd.x)}
              y={Math.min(drawStart.y, drawEnd.y)}
              width={Math.abs(drawEnd.x - drawStart.x)}
              height={Math.abs(drawEnd.y - drawStart.y)}
              fill="rgba(101, 163, 13, 0.1)"
              stroke="#65A30D"
              strokeWidth="2"
              strokeDasharray="6 3"
              rx="4"
            />
          )}
          {drawStart && drawEnd && activeTool === 'circle' && (() => {
            const r = Math.sqrt(Math.pow(drawEnd.x - drawStart.x, 2) + Math.pow(drawEnd.y - drawStart.y, 2));
            return (
              <circle
                cx={drawStart.x}
                cy={drawStart.y}
                r={r}
                fill="rgba(101, 163, 13, 0.1)"
                stroke="#65A30D"
                strokeWidth="2"
                strokeDasharray="6 3"
              />
            );
          })()}
          {drawStart && drawEnd && activeTool === 'arrow' && (() => {
            const dx = drawEnd.x - drawStart.x;
            const dy = drawEnd.y - drawStart.y;
            const len = Math.sqrt(dx * dx + dy * dy);
            if (len < 1) return null;
            const ux = dx / len;
            const uy = dy / len;
            const headLen = 12;
            const headW = 6;
            const baseX = drawEnd.x - ux * headLen;
            const baseY = drawEnd.y - uy * headLen;
            return (
              <>
                <line x1={drawStart.x} y1={drawStart.y} x2={baseX} y2={baseY} stroke="#65A30D" strokeWidth="2" strokeDasharray="6 3" />
                <polygon points={`${drawEnd.x},${drawEnd.y} ${baseX - uy * headW},${baseY + ux * headW} ${baseX + uy * headW},${baseY - ux * headW}`} fill="#65A30D" opacity="0.6" />
              </>
            );
          })()}

          {/* Marquee selection preview */}
          {marqueeStart && marqueeEnd && activeTool === 'pointer' && (
            <rect
              x={Math.min(marqueeStart.x, marqueeEnd.x)}
              y={Math.min(marqueeStart.y, marqueeEnd.y)}
              width={Math.abs(marqueeEnd.x - marqueeStart.x)}
              height={Math.abs(marqueeEnd.y - marqueeStart.y)}
              fill="rgba(59, 130, 246, 0.08)"
              stroke="#3B82F6"
              strokeWidth="1.5"
              strokeDasharray="6 3"
              rx="2"
            />
          )}
        </svg>

        {/* Inline text editing overlay */}
        {editingText && (() => {
          const ann = annotations.find(a => a.id === editingText.id);
          if (!ann) return null;
          const fontSize = ann.fontSize || 14;
          return (
            <input
              ref={textInputRef}
              type="text"
              defaultValue={ann.text || ''}
              autoFocus
              className="absolute bg-transparent outline-none font-black tracking-tight"
              style={{
                left: ann.x,
                top: ann.y - fontSize,
                fontSize,
                color: ann.color || (effectiveMode === 'dark' ? 'white' : '#1C1917'),
                minWidth: 60,
                caretColor: effectiveMode === 'dark' ? '#84CC16' : '#65A30D',
                border: 'none',
                padding: 0,
                margin: 0,
                lineHeight: 1,
              }}
              onBlur={(e) => {
                if (textEditCommittedRef.current) return;
                textEditCommittedRef.current = true;
                const val = e.target.value.trim();
                if (val && onUpdateAnnotation) {
                  onUpdateAnnotation(editingText.id, { text: val });
                } else if (!val) {
                  onDelete(editingText.id);
                }
                setEditingText(null);
              }}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === 'Enter') {
                  (e.target as HTMLInputElement).blur();
                } else if (e.key === 'Escape') {
                  textEditCommittedRef.current = true;
                  const ann = annotations.find(a => a.id === editingText.id);
                  if (!ann?.text) {
                    onDelete(editingText.id);
                  }
                  setEditingText(null);
                }
              }}
              onMouseDown={(e) => e.stopPropagation()}
            />
          );
        })()}

        {nodes.map(node => {
          // Check if this is an external network node
          if (isExternalNetworkNode(node)) {
            // Render external network node with cloud shape
            const extNode = node;
            const vlanLabel = extNode.managedInterfaceName
              ? extNode.managedInterfaceName
              : extNode.connectionType === 'vlan'
                ? `VLAN ${extNode.vlanId || '?'}`
                : extNode.bridgeName || 'Unconfigured';

            return (
              <div
                key={node.id}
                style={{ left: node.x - 28, top: node.y - 20 }}
                onMouseDown={(e) => handleNodeMouseDown(e, node.id)}
                onMouseUp={(e) => handleNodeMouseUp(e, node.id)}
                onContextMenu={(e) => handleNodeContextMenu(e, node.id)}
                className={`absolute w-14 h-10 flex items-center justify-center cursor-pointer shadow-md transition-[box-shadow,background-color,border-color,transform] duration-150 rounded-2xl
                  ${(selectedId === node.id || selectedIds?.has(node.id))
                    ? 'ring-2 ring-blue-500 bg-gradient-to-br from-blue-100 to-purple-100 dark:from-blue-900/60 dark:to-purple-900/60 shadow-lg shadow-blue-500/20'
                    : 'bg-gradient-to-br from-blue-50 to-purple-50 dark:from-blue-950/40 dark:to-purple-950/40 border border-blue-300 dark:border-blue-700'}
                  ${linkingNode === node.id ? 'ring-2 ring-blue-400 scale-110' : ''}
                  hover:border-blue-400 z-10 select-none group`}
              >
                <i className="fa-solid fa-cloud text-blue-500 dark:text-blue-400 text-lg"></i>
                <div className="absolute top-full mt-1 text-[10px] font-bold text-blue-700 dark:text-blue-300 bg-white/90 dark:bg-stone-900/80 px-1.5 rounded shadow-sm border border-blue-200 dark:border-blue-800 whitespace-nowrap pointer-events-none">
                  {node.name}
                </div>
                <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-[8px] font-bold text-blue-500 dark:text-blue-400 bg-white/80 dark:bg-stone-900/80 px-1 rounded whitespace-nowrap pointer-events-none">
                  {vlanLabel}
                </div>
              </div>
            );
          }

          // Regular device node rendering
          const deviceNode = node as import('../types').DeviceNode;
          const status = runtimeStates[node.id];
          const isRouter = isDeviceNode(node) && deviceNode.type === DeviceType.ROUTER;
          const isSwitch = isDeviceNode(node) && deviceNode.type === DeviceType.SWITCH;

          let borderRadius = '8px';
          if (isRouter) borderRadius = '50%';
          if (isSwitch) borderRadius = '4px';

          // Status indicator: green=running, gray=stopped, yellow=booting, orange=stopping, red=error, no dot=undeployed
          const getStatusDot = () => {
            const ns = nodeStates?.[node.id];
            const imageSyncActive =
              ns?.image_sync_status === 'syncing' || ns?.image_sync_status === 'checking';
            if (!status && !imageSyncActive) return null; // No runtime state and no sync = undeployed
            let dotColor = '#a8a29e'; // stone-400 (stopped)
            let animate = false;
            if (status === 'running') dotColor = '#22c55e'; // green-500
            else if (status === 'booting') { dotColor = '#eab308'; animate = true; } // yellow-500
            else if (status === 'stopping') { dotColor = '#f97316'; animate = true; } // orange-500
            else if (status === 'error') dotColor = '#ef4444'; // red-500
            // Make image sync visually distinct from normal booting.
            if (imageSyncActive) {
              dotColor = '#3b82f6'; // blue-500
              animate = true;
            }

            // Build tooltip with retry info and elapsed time
            let tooltip: string = status || 'unknown';
            if (imageSyncActive) {
              const phase = ns?.image_sync_status === 'checking' ? 'checking' : 'syncing';
              tooltip = `image ${phase}`;
              if (ns?.image_sync_message) {
                tooltip += `: ${ns.image_sync_message}`;
              }
            }
            if (ns?.will_retry && (ns.enforcement_attempts ?? 0) > 0) {
              const max = ns.max_enforcement_attempts ?? 0;
              tooltip = max > 0
                ? `Starting (attempt ${ns.enforcement_attempts}/${max})`
                : `Starting (attempt ${ns.enforcement_attempts})`;
            }
            // Add elapsed time for transitional states
            if ((status === 'booting' || status === 'stopping') && ns?.starting_started_at) {
              const elapsed = Math.floor((Date.now() - new Date(ns.starting_started_at).getTime()) / 1000);
              if (elapsed > 0) {
                const mins = Math.floor(elapsed / 60);
                const secs = elapsed % 60;
                const timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
                tooltip += ` (${timeStr})`;
              }
            }

            return (
              <div
                className={`absolute -top-1 -right-1 w-3 h-3 rounded-full border-2 border-white dark:border-stone-800 shadow-sm ${animate ? 'animate-pulse' : ''}`}
                style={{ backgroundColor: dotColor, transition: 'background-color 300ms ease-in-out' }}
                title={tooltip}
              />
            );
          };

          // Agent indicator: shows which agent the node is running on
          const getAgentIndicator = () => {
            // Only show when enabled, multiple agents exist, and node has a host assigned
            if (!showAgentIndicators || agents.length <= 1) return null;
            const nodeState = nodeStates[node.id];
            if (!nodeState?.host_id || !nodeState?.host_name) return null;
            const color = getAgentColor(nodeState.host_id);
            const initials = getAgentInitials(nodeState.host_name);
            return (
              <div
                className="absolute -bottom-1 -left-1 w-4 h-4 rounded-full border-2 border-white dark:border-stone-800 shadow-sm flex items-center justify-center text-[7px] font-bold text-white"
                style={{ backgroundColor: color }}
                title={`Running on: ${nodeState.host_name}`}
              >
                {initials}
              </div>
            );
          };

          // Error indicator icon overlay
          const getErrorIndicator = () => {
            if (status !== 'error') return null;
            if (!errorIndicatorSettings?.showIcon) return null;
            const nodeState = nodeStates[node.id];
            const errorMessage = nodeState?.error_message || 'Node error';
            return (
              <div
                className="absolute -top-2 -right-2 w-5 h-5 bg-red-500 rounded-full flex items-center justify-center shadow-md cursor-pointer z-20"
                title={errorMessage}
              >
                <i className="fa-solid fa-exclamation text-white text-[10px]" />
              </div>
            );
          };

          // Error border class
          const errorBorderClass = status === 'error' && errorIndicatorSettings?.showBorder
            ? `ring-2 ring-red-500 ${errorIndicatorSettings?.pulseAnimation ? 'node-error-pulse' : ''}`
            : '';

          return (
            <div
              key={node.id}
              style={{ left: node.x - 24, top: node.y - 24, borderRadius }}
              onMouseDown={(e) => handleNodeMouseDown(e, node.id)}
              onMouseUp={(e) => handleNodeMouseUp(e, node.id)}
              onContextMenu={(e) => handleNodeContextMenu(e, node.id)}
              className={`absolute w-12 h-12 flex items-center justify-center cursor-pointer shadow-sm transition-[box-shadow,background-color,border-color,transform] duration-150
                ${(selectedId === node.id || selectedIds?.has(node.id)) ? 'ring-2 ring-sage-500 bg-sage-500/10 dark:bg-sage-900/40 shadow-lg shadow-sage-500/20' : 'bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-600'}
                ${status === 'running' ? 'border-green-500/50 shadow-md shadow-green-500/10' : ''}
                ${linkingNode === node.id ? 'ring-2 ring-sage-400 scale-110' : ''}
                ${errorBorderClass}
                hover:border-sage-400 z-10 select-none group`}
            >
              <div
                className={`flex items-center justify-center ${isRouter ? 'w-8 h-8 rounded-full' : 'w-8 h-8 rounded-md'} border ${
                  effectiveMode === 'dark'
                    ? 'bg-stone-700/40 border-stone-600/80'
                    : 'bg-stone-100/90 border-stone-400/90 shadow-[0_1px_2px_rgba(28,25,23,0.18)]'
                }`}
              >
                <i className={`fa-solid ${getNodeIcon(deviceNode.model)} ${status === 'running' ? 'text-green-500 dark:text-green-400' : status === 'error' ? 'text-red-500 dark:text-red-400' : 'text-stone-700 dark:text-stone-100'} ${isRouter || isSwitch ? 'text-xl' : 'text-lg'}`}></i>
              </div>
              {getStatusDot()}
              {getAgentIndicator()}
              {getErrorIndicator()}
              {highlightedNodeNames?.has(node.name) && (
                <div
                  className="absolute inset-[-6px] rounded-full border-2 border-blue-500 animate-pulse pointer-events-none"
                  style={{ borderRadius }}
                />
              )}
              <div className="absolute top-full mt-1 text-[10px] font-bold text-stone-700 dark:text-stone-300 bg-white/90 dark:bg-stone-900/80 px-1 rounded shadow-sm border border-stone-200 dark:border-stone-700 whitespace-nowrap pointer-events-none">
                {node.name}
              </div>
            </div>
          );
        })}
      </div>

      <div className="absolute bottom-6 left-6 flex flex-col gap-2 z-30">
        <div className="bg-white/80 dark:bg-stone-900/80 backdrop-blur-md border border-stone-200 dark:border-stone-700 rounded-lg flex flex-col overflow-hidden shadow-lg">
          <button onClick={() => setZoom(prev => Math.min(prev * 1.2, 5))} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors border-b border-stone-200 dark:border-stone-700"><i className="fa-solid fa-plus"></i></button>
          <button onClick={() => setZoom(prev => Math.max(prev / 1.2, 0.1))} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"><i className="fa-solid fa-minus"></i></button>
        </div>
        <div className="bg-white/80 dark:bg-stone-900/80 backdrop-blur-md border border-stone-200 dark:border-stone-700 rounded-lg flex flex-col overflow-hidden shadow-lg">
          <button title="Center (zoom out if needed)" onClick={centerCanvas} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors border-b border-stone-200 dark:border-stone-700"><i className="fa-solid fa-crosshairs"></i></button>
          <button title="Fit to screen" onClick={fitToScreen} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"><i className="fa-solid fa-maximize"></i></button>
        </div>
        {/* Agent indicator toggle - only show when multiple agents */}
        {agents.length > 1 && onToggleAgentIndicators && (
          <div className="bg-white/80 dark:bg-stone-900/80 backdrop-blur-md border border-stone-200 dark:border-stone-700 rounded-lg flex flex-col overflow-hidden shadow-lg">
            <button
              onClick={onToggleAgentIndicators}
              className={`p-3 transition-colors ${showAgentIndicators ? 'text-sage-600 dark:text-sage-400 bg-sage-500/10' : 'text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800'}`}
              title={showAgentIndicators ? 'Hide agent indicators' : 'Show agent indicators'}
            >
              <i className="fa-solid fa-server"></i>
            </button>
          </div>
        )}
      </div>

      {contextMenu && (
        <div className="fixed z-[100] w-52 bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded-xl shadow-2xl py-2 animate-in fade-in zoom-in duration-100" style={{ left: contextMenu.x, top: contextMenu.y }} onMouseDown={(e) => e.stopPropagation()}>
          <div className="px-4 py-2 border-b border-stone-100 dark:border-stone-800 mb-1 flex items-center justify-between">
            <span className="text-[10px] font-black text-stone-400 dark:text-stone-500 uppercase tracking-widest">
              {contextMenu.type === 'node'
                ? (isExternalNetworkNode(nodeMap.get(contextMenu.id)!) ? 'External Network' : 'Node Actions')
                : 'Link Actions'}
            </span>
          </div>
          {contextMenu.type === 'node' && (() => {
            const contextNode = nodeMap.get(contextMenu.id);
            // External network nodes only have delete action
            if (contextNode && isExternalNetworkNode(contextNode)) {
              return null;
            }
            const nodeStatus = runtimeStates[contextMenu.id] || 'stopped';
            const isNodeRunning = nodeStatus === 'running' || nodeStatus === 'booting';
            return (
              <>
                <button onClick={() => handleAction('console')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-stone-700 dark:text-stone-300 hover:bg-sage-600 hover:text-white transition-colors">
                  <i className="fa-solid fa-terminal w-4"></i> Open Console
                </button>
                <button onClick={() => handleAction('extract-config')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-stone-700 dark:text-stone-300 hover:bg-sage-600 hover:text-white transition-colors">
                  <i className="fa-solid fa-download w-4"></i> Extract Config
                </button>
                {!isNodeRunning && (
                  <button onClick={() => handleAction('start')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-green-600 dark:text-green-400 hover:bg-green-600 hover:text-white transition-colors">
                    <i className="fa-solid fa-play w-4"></i> Start Node
                  </button>
                )}
                {isNodeRunning && (
                  <button onClick={() => handleAction('stop')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-red-600 dark:text-red-400 hover:bg-red-600 hover:text-white transition-colors">
                    <i className="fa-solid fa-power-off w-4"></i> Stop Node
                  </button>
                )}
                <div className="h-px bg-stone-100 dark:bg-stone-800 my-1 mx-2"></div>
              </>
            );
          })()}
          <button onClick={() => handleAction('delete')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-red-600 dark:text-red-500 hover:bg-red-600 hover:text-white transition-colors">
            <i className="fa-solid fa-trash-can w-4"></i>
            {contextMenu.type === 'node'
              ? (nodeMap.get(contextMenu.id) && isExternalNetworkNode(nodeMap.get(contextMenu.id)!)
                  ? 'Remove External Network'
                  : 'Remove Device')
              : 'Delete Connection'}
          </button>
        </div>
      )}
    </div>
  );
};

export default memo(Canvas);
