import { useState, useCallback, useRef } from 'react';
import { Node, Annotation, CanvasTool, AnnotationType, DeviceModel } from '../../types';
import { ResizeHandle, ResizeState } from './types';

interface UseCanvasInteractionArgs {
  containerRef: React.RefObject<HTMLDivElement | null>;
  zoom: number;
  setZoom: React.Dispatch<React.SetStateAction<number>>;
  offset: { x: number; y: number };
  setOffset: React.Dispatch<React.SetStateAction<{ x: number; y: number }>>;
  nodes: Node[];
  annotations: Annotation[];
  activeTool: CanvasTool;
  onToolCreate?: (type: AnnotationType, x: number, y: number, opts?: { width?: number; height?: number; targetX?: number; targetY?: number }) => void;
  onNodeMove: (id: string, x: number, y: number) => void;
  onAnnotationMove: (id: string, x: number, y: number) => void;
  onConnect: (sourceId: string, targetId: string) => void;
  onSelect: (id: string | null) => void;
  onSelectMultiple?: (ids: Set<string>) => void;
  onUpdateAnnotation?: (id: string, updates: Partial<Annotation>) => void;
  onDropDevice?: (model: DeviceModel, x: number, y: number) => void;
  onDropExternalNetwork?: (x: number, y: number) => void;
}

export function useCanvasInteraction({
  containerRef,
  zoom,
  setZoom,
  offset,
  setOffset,
  nodes,
  annotations,
  activeTool,
  onToolCreate,
  onNodeMove,
  onAnnotationMove,
  onConnect,
  onSelect,
  onSelectMultiple,
  onUpdateAnnotation,
  onDropDevice,
  onDropExternalNetwork,
}: UseCanvasInteractionArgs) {
  const [draggingNode, setDraggingNode] = useState<string | null>(null);
  const [draggingAnnotation, setDraggingAnnotation] = useState<string | null>(null);
  const [linkingNode, setLinkingNode] = useState<string | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
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
    onSelect(id);
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

  return {
    draggingNode,
    linkingNode,
    mousePos,
    isPanning,
    resizing,
    drawStart,
    drawEnd,
    editingText,
    setEditingText,
    pendingTextEditRef,
    textEditCommittedRef,
    textInputRef,
    marqueeStart,
    marqueeEnd,
    handleMouseMove,
    handleWheel,
    handleMouseDown,
    handleMouseUp,
    handleNodeMouseDown,
    handleAnnotationMouseDown,
    handleResizeMouseDown,
    getResizeCursor,
    handleNodeMouseUp,
    handleLinkMouseDown,
    handleDragOver,
    handleDrop,
  };
}
