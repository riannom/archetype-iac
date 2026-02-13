import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ConsoleWindow, Node } from '../types';
import TerminalSession from './TerminalSession';
import { NodeStateEntry } from '../../types/nodeState';

const VIEWPORT_MARGIN_PX = 8;

function clamp(n: number, min: number, max: number) {
  return Math.min(Math.max(n, min), max);
}

interface ConsoleManagerProps {
  labId: string;
  windows: ConsoleWindow[];
  nodes: Node[];
  nodeStates?: Record<string, NodeStateEntry>;
  isVisible?: boolean;
  onCloseWindow: (windowId: string) => void;
  onCloseTab: (windowId: string, nodeId: string) => void;
  onSetActiveTab: (windowId: string, nodeId: string) => void;
  onUpdateWindowPos: (windowId: string, x: number, y: number) => void;
  onUpdateWindowSize?: (windowId: string, width: number, height: number) => void;
  onMergeWindows?: (sourceWindowId: string, targetWindowId: string) => void;
  onSplitTab?: (windowId: string, deviceId: string, x: number, y: number) => void;
  onReorderTab?: (windowId: string, fromIndex: number, toIndex: number) => void;
  onToggleMinimize?: (windowId: string) => void;
  onDockWindow?: (windowId: string) => void;
}

// Threshold in pixels before a tab drag initiates a split
const TAB_DRAG_THRESHOLD = 30;
// Threshold for horizontal movement to trigger reorder mode
const REORDER_THRESHOLD = 10;
// Distance from bottom of viewport to show dock zone (in pixels)
const DOCK_ZONE_HEIGHT = 100;

const ConsoleManager: React.FC<ConsoleManagerProps> = ({
  labId,
  windows,
  nodes,
  nodeStates = {},
  isVisible = true,
  onCloseWindow,
  onCloseTab,
  onSetActiveTab,
  onUpdateWindowPos,
  onMergeWindows,
  onSplitTab,
  onReorderTab,
  onToggleMinimize,
  onDockWindow,
}) => {
  const showConsoles = isVisible;

  const [viewport, setViewport] = useState(() => ({
    w: typeof window !== 'undefined' ? window.innerWidth : 0,
    h: typeof window !== 'undefined' ? window.innerHeight : 0,
  }));
  const viewportRef = useRef(viewport);
  useEffect(() => {
    viewportRef.current = viewport;
  }, [viewport]);
  useEffect(() => {
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Window focus + stacking order (higher zIndex on most recently interacted window)
  const [focusedWindowId, setFocusedWindowId] = useState<string | null>(null);
  const [zOrder, setZOrder] = useState<string[]>([]);
  useEffect(() => {
    const ids = windows.map((w) => w.id);
    setZOrder((prev) => {
      const kept = prev.filter((id) => ids.includes(id));
      const appended = ids.filter((id) => !kept.includes(id));
      return [...kept, ...appended];
    });
    setFocusedWindowId((prev) => {
      if (prev && ids.includes(prev)) return prev;
      return ids.length ? ids[ids.length - 1] : null;
    });
  }, [windows]);

  const bringToFront = useCallback((windowId: string) => {
    setFocusedWindowId(windowId);
    setZOrder((prev) => {
      const next = prev.filter((id) => id !== windowId);
      next.push(windowId);
      return next;
    });
  }, []);

  // Ref-based drag/resize tracking (no re-renders during movement)
  const dragRef = useRef<{ id: string; startX: number; startY: number; originX: number; originY: number } | null>(null);
  const resizeRef = useRef<{ id: string; startWidth: number; startHeight: number; startX: number; startY: number } | null>(null);
  const dragPerfRef = useRef<{ enabled: boolean; start: number; lastSample: number; frames: number } | null>(null);

  // Boolean state for CSS shadow (only changes on start/end)
  const [isDragging, setIsDragging] = useState<string | null>(null);
  const [isResizing, setIsResizing] = useState<string | null>(null);
  const [winSizes, setWinSizes] = useState<Record<string, { w: number; h: number }>>({});

  // Drop target detection state
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  // Dock zone detection (for dropping onto bottom panel)
  const [showDockZone, setShowDockZone] = useState(false);
  const dropTargetIdRef = useRef<string | null>(null);
  const showDockZoneRef = useRef(false);
  const windowRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // Tab drag state for splitting tabs out of windows or reordering within
  const [tabDragState, setTabDragState] = useState<{
    windowId: string;
    deviceId: string;
    startX: number;
    startY: number;
    currentX: number;
    currentY: number;
    isDragging: boolean; // true once split threshold is exceeded (vertical)
    isReordering: boolean; // true for horizontal drag within header
    reorderTargetIndex: number | null; // drop position indicator
  } | null>(null);

  // Refs to track tab elements for calculating drop positions
  const tabRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // rAF gating ref
  const rafRef = useRef<number | null>(null);

  // Callback prop refs (avoid stale closures in global listeners)
  const onUpdateWindowPosRef = useRef(onUpdateWindowPos);
  useEffect(() => { onUpdateWindowPosRef.current = onUpdateWindowPos; }, [onUpdateWindowPos]);
  const onMergeWindowsRef = useRef(onMergeWindows);
  useEffect(() => { onMergeWindowsRef.current = onMergeWindows; }, [onMergeWindows]);
  const onSplitTabRef = useRef(onSplitTab);
  useEffect(() => { onSplitTabRef.current = onSplitTab; }, [onSplitTab]);
  const onReorderTabRef = useRef(onReorderTab);
  useEffect(() => { onReorderTabRef.current = onReorderTab; }, [onReorderTab]);
  const onDockWindowRef = useRef(onDockWindow);
  useEffect(() => { onDockWindowRef.current = onDockWindow; }, [onDockWindow]);
  const windowsRef = useRef(windows);
  useEffect(() => { windowsRef.current = windows; }, [windows]);

  // Store ref for a window element
  const setWindowRef = useCallback((windowId: string, element: HTMLDivElement | null) => {
    if (element) {
      windowRefs.current.set(windowId, element);
    } else {
      windowRefs.current.delete(windowId);
    }
  }, []);

  // Find which window (if any) the cursor is over, excluding the source window
  const findDropTarget = useCallback((clientX: number, clientY: number, excludeWindowId: string): string | null => {
    for (const [windowId, element] of windowRefs.current.entries()) {
      if (windowId === excludeWindowId) continue;
      const rect = element.getBoundingClientRect();
      if (
        clientX >= rect.left &&
        clientX <= rect.right &&
        clientY >= rect.top &&
        clientY <= rect.bottom
      ) {
        return windowId;
      }
    }
    return null;
  }, []);

  const handleMouseDown = (e: React.MouseEvent, win: ConsoleWindow, originX: number, originY: number) => {
    bringToFront(win.id);
    dragRef.current = {
      id: win.id,
      startX: e.clientX - originX,
      startY: e.clientY - originY,
      originX,
      originY,
    };
    const perfEnabled = localStorage.getItem('consoleDragPerf') === '1';
    dragPerfRef.current = perfEnabled
      ? { enabled: true, start: performance.now(), lastSample: performance.now(), frames: 0 }
      : { enabled: false, start: 0, lastSample: 0, frames: 0 };
    setIsDragging(win.id);
  };

  const handleResizeMouseDown = (
    e: React.MouseEvent,
    win: ConsoleWindow,
    originX: number,
    originY: number,
    displayedW: number,
    displayedH: number
  ) => {
    e.stopPropagation();
    e.preventDefault();
    // Use the rendered (viewport-clamped) size as the baseline to avoid resize jumps on narrow viewports.
    const currentW = displayedW;
    const currentH = displayedH;
    resizeRef.current = {
      id: win.id,
      startWidth: currentW,
      startHeight: currentH,
      startX: e.clientX,
      startY: e.clientY,
      // Stash origin so we can clamp max size relative to the window's current position.
      ...( { originX, originY } as any ),
    };
    setIsResizing(win.id);
  };

  // Handle tab mousedown for potential split drag or reorder
  const handleTabMouseDown = (e: React.MouseEvent, win: ConsoleWindow, deviceId: string) => {
    // Only allow tab drag if window has multiple tabs and at least one handler is available
    if (win.deviceIds.length <= 1 || (!onSplitTab && !onReorderTab)) return;

    e.stopPropagation();
    setTabDragState({
      windowId: win.id,
      deviceId,
      startX: e.clientX,
      startY: e.clientY,
      currentX: e.clientX,
      currentY: e.clientY,
      isDragging: false,
      isReordering: false,
      reorderTargetIndex: null,
    });
  };

  // Calculate the target index for reordering based on cursor position
  const calculateReorderIndex = useCallback((clientX: number, windowId: string): number => {
    const win = windowsRef.current.find(w => w.id === windowId);
    if (!win) return 0;

    let targetIndex = 0;
    for (let i = 0; i < win.deviceIds.length; i++) {
      const tabEl = tabRefs.current.get(`${windowId}-${win.deviceIds[i]}`);
      if (!tabEl) continue;
      const rect = tabEl.getBoundingClientRect();
      const midpoint = rect.left + rect.width / 2;
      if (clientX > midpoint) targetIndex = i + 1;
    }
    return targetIndex;
  }, []);

  // Global mouse listeners — registered when any drag/resize is active
  useEffect(() => {
    const isActive = isDragging || isResizing || tabDragState;
    if (!isActive) return;

    const handleMouseMove = (e: MouseEvent) => {
      // Gate all movement updates behind rAF
      if (rafRef.current) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;

        // Handle window drag — direct DOM update
        if (dragRef.current) {
          const newX = e.clientX - dragRef.current.startX;
          const newY = e.clientY - dragRef.current.startY;

          // Direct DOM manipulation for smooth dragging
          const el = windowRefs.current.get(dragRef.current.id);
          if (el) {
            const dx = newX - dragRef.current.originX;
            const dy = newY - dragRef.current.originY;
            el.style.transform = `translate3d(${dx}px, ${dy}px, 0)`;
            el.style.willChange = 'transform';
          }

          // Store position for mouseup commit
          (dragRef.current as any)._lastX = newX;
          (dragRef.current as any)._lastY = newY;

          // Check for drop target (for window merge)
          if (onMergeWindowsRef.current) {
            const target = findDropTarget(e.clientX, e.clientY, dragRef.current.id);
            if (dropTargetIdRef.current !== target) {
              dropTargetIdRef.current = target;
              setDropTargetId(target);
            }
          }

          // Check if near bottom of viewport for dock zone
          if (onDockWindowRef.current) {
            const viewportBottom = window.innerHeight;
            const isNearBottom = e.clientY > viewportBottom - DOCK_ZONE_HEIGHT;
            if (showDockZoneRef.current !== isNearBottom) {
              showDockZoneRef.current = isNearBottom;
              setShowDockZone(isNearBottom);
            }
          }

          if (dragPerfRef.current?.enabled) {
            dragPerfRef.current.frames += 1;
            const now = performance.now();
            if (now - dragPerfRef.current.lastSample > 1000) {
              const elapsed = now - dragPerfRef.current.start;
              const fps = (dragPerfRef.current.frames / elapsed) * 1000;
              console.debug(`[console-drag] fps=${fps.toFixed(1)} elapsed=${(elapsed / 1000).toFixed(1)}s`);
              dragPerfRef.current.lastSample = now;
            }
          }

        }

        // Handle window resize — direct DOM update
        if (resizeRef.current) {
          const deltaX = e.clientX - resizeRef.current.startX;
          const deltaY = e.clientY - resizeRef.current.startY;
          const vp = viewportRef.current;
          const originX = (resizeRef.current as any).originX as number | undefined;
          const originY = (resizeRef.current as any).originY as number | undefined;
          const maxW = vp.w > 0 && originX !== undefined
            ? Math.max(0, vp.w - VIEWPORT_MARGIN_PX - originX)
            : Number.POSITIVE_INFINITY;
          const maxH = vp.h > 0 && originY !== undefined
            ? Math.max(0, vp.h - VIEWPORT_MARGIN_PX - originY)
            : Number.POSITIVE_INFINITY;

          // Keep min <= max so tiny viewports don't force overflow.
          const minW = Math.min(320, maxW);
          const minH = Math.min(240, maxH);

          const newW = clamp(resizeRef.current.startWidth + deltaX, minW, maxW);
          const newH = clamp(resizeRef.current.startHeight + deltaY, minH, maxH);

          // Direct DOM manipulation for smooth resizing
          const el = windowRefs.current.get(resizeRef.current.id);
          if (el) {
            el.style.width = `${newW}px`;
            el.style.height = `${newH}px`;
          }

          // Store for mouseup commit
          (resizeRef.current as any)._lastW = newW;
          (resizeRef.current as any)._lastH = newH;
        }

        // Handle tab drag for splitting OR reordering
        if (tabDragState) {
          const deltaX = e.clientX - tabDragState.startX;
          const deltaY = e.clientY - tabDragState.startY;
          const absX = Math.abs(deltaX);
          const absY = Math.abs(deltaY);

          setTabDragState((prev) => {
            if (!prev) return null;

            // If vertical movement exceeds threshold → split mode (existing behavior)
            if (absY >= TAB_DRAG_THRESHOLD && !prev.isReordering) {
              return {
                ...prev,
                currentX: e.clientX,
                currentY: e.clientY,
                isDragging: true,
                isReordering: false,
                reorderTargetIndex: null,
              };
            }

            // If already in split mode, continue tracking position
            if (prev.isDragging) {
              return {
                ...prev,
                currentX: e.clientX,
                currentY: e.clientY,
              };
            }

            // If horizontal movement detected and not in split mode → reorder mode
            if (absX >= REORDER_THRESHOLD && !prev.isDragging) {
              const targetIndex = calculateReorderIndex(e.clientX, prev.windowId);
              return {
                ...prev,
                currentX: e.clientX,
                currentY: e.clientY,
                isReordering: true,
                reorderTargetIndex: targetIndex,
              };
            }

            // If already in reorder mode, update target index
            if (prev.isReordering) {
              const targetIndex = calculateReorderIndex(e.clientX, prev.windowId);
              return {
                ...prev,
                currentX: e.clientX,
                currentY: e.clientY,
                reorderTargetIndex: targetIndex,
              };
            }

            // Not enough movement yet
            return {
              ...prev,
              currentX: e.clientX,
              currentY: e.clientY,
            };
          });
        }
      });
    };

    const handleMouseUp = (e: MouseEvent) => {
      // Cancel any pending rAF
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }

      // Commit drag position to React state
      if (dragRef.current) {
        let lastX = (dragRef.current as any)._lastX;
        let lastY = (dragRef.current as any)._lastY;
        const el = windowRefs.current.get(dragRef.current.id);
        const rect = el?.getBoundingClientRect();
        const vp = viewportRef.current;

        // Clamp committed position so fixed windows can't create page-level overflow.
        if (rect && vp.w > 0) {
          const maxLeft = Math.max(VIEWPORT_MARGIN_PX, vp.w - rect.width - VIEWPORT_MARGIN_PX);
          lastX = clamp(lastX ?? VIEWPORT_MARGIN_PX, VIEWPORT_MARGIN_PX, maxLeft);
        }
        if (rect && vp.h > 0) {
          const maxTop = Math.max(VIEWPORT_MARGIN_PX, vp.h - rect.height - VIEWPORT_MARGIN_PX);
          lastY = clamp(lastY ?? VIEWPORT_MARGIN_PX, VIEWPORT_MARGIN_PX, maxTop);
        }

        if (lastX !== undefined && lastY !== undefined) {
          onUpdateWindowPosRef.current(dragRef.current.id, lastX, lastY);
        }
        if (el) {
          el.style.transform = '';
          el.style.willChange = 'auto';
        }

        // Handle dock to bottom panel on drop
        if (showDockZoneRef.current && onDockWindowRef.current) {
          onDockWindowRef.current(dragRef.current.id);
        }
        // Handle window merge on drop
        else if (dropTargetIdRef.current && onMergeWindowsRef.current) {
          onMergeWindowsRef.current(dragRef.current.id, dropTargetIdRef.current);
        }
      }

      // Commit resize to state
      if (resizeRef.current) {
        const resizeId = resizeRef.current.id;
        const lastW = (resizeRef.current as any)._lastW;
        const lastH = (resizeRef.current as any)._lastH;
        if (lastW !== undefined && lastH !== undefined) {
          setWinSizes((prev) => ({
            ...prev,
            [resizeId]: { w: lastW, h: lastH },
          }));
        }
      }

      // Handle tab reorder on drop
      if (tabDragState?.isReordering && tabDragState.reorderTargetIndex !== null && onReorderTabRef.current) {
        const win = windowsRef.current.find(w => w.id === tabDragState.windowId);
        if (win) {
          const fromIndex = win.deviceIds.indexOf(tabDragState.deviceId);
          const toIndex = tabDragState.reorderTargetIndex;
          if (fromIndex !== -1 && fromIndex !== toIndex && fromIndex !== toIndex - 1) {
            onReorderTabRef.current(tabDragState.windowId, fromIndex, toIndex);
          }
        }
      }
      // Handle tab split on drop
      else if (tabDragState?.isDragging && onSplitTabRef.current) {
        onSplitTabRef.current(tabDragState.windowId, tabDragState.deviceId, e.clientX - 260, e.clientY - 50);
      }

      dragRef.current = null;
      dragPerfRef.current = null;
      resizeRef.current = null;
      setIsDragging(null);
      setIsResizing(null);
      setDropTargetId(null);
      setShowDockZone(false);
      dropTargetIdRef.current = null;
      showDockZoneRef.current = false;
      setTabDragState(null);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [isDragging, isResizing, tabDragState, findDropTarget, calculateReorderIndex]);

  // Get the node being dragged as a tab (for ghost preview)
  const tabDragNode = tabDragState?.isDragging
    ? nodes.find((n) => n.id === tabDragState.deviceId)
    : null;

  return (
    <>
      {windows.map((win) => {
        const size = winSizes[win.id] || { w: 520, h: 360 };
        const activeNode = nodes.find((n) => n.id === win.activeDeviceId);
        const isDropTarget = dropTargetId === win.id;
        const isBeingDraggedOverTarget = isDragging === win.id && dropTargetId !== null;
        const isMinimized = !win.isExpanded;
        const isMoving = isDragging === win.id || isResizing === win.id;
        const dragState = dragRef.current && dragRef.current.id === win.id ? (dragRef.current as any) : null;
        const resizeState = resizeRef.current && resizeRef.current.id === win.id ? (resizeRef.current as any) : null;
        const desiredW = resizeState?._lastW ?? (isMinimized ? 280 : size.w);
        const desiredH = resizeState?._lastH ?? (isMinimized ? 36 : size.h);

        // Fixed-position elements can still contribute to document overflow in some browsers.
        // Clamp size/position to viewport so opening a console can't introduce horizontal scrolling.
        const vpW = viewport.w || (typeof window !== 'undefined' ? window.innerWidth : 0);
        const vpH = viewport.h || (typeof window !== 'undefined' ? window.innerHeight : 0);
        const hasViewport = vpW > 0 && vpH > 0;

        const maxW = hasViewport ? Math.max(0, vpW - VIEWPORT_MARGIN_PX * 2) : Number.POSITIVE_INFINITY;
        const maxH = hasViewport ? Math.max(0, vpH - VIEWPORT_MARGIN_PX * 2) : Number.POSITIVE_INFINITY;
        const minW = Math.min(isMinimized ? 200 : 320, maxW);
        const minH = Math.min(isMinimized ? 36 : 240, maxH);

        const width = clamp(desiredW, minW, maxW);
        const height = clamp(desiredH, minH, maxH);

        const minLeft = hasViewport ? VIEWPORT_MARGIN_PX : -Number.POSITIVE_INFINITY;
        const minTop = hasViewport ? VIEWPORT_MARGIN_PX : -Number.POSITIVE_INFINITY;
        const maxLeft = hasViewport ? Math.max(VIEWPORT_MARGIN_PX, vpW - width - VIEWPORT_MARGIN_PX) : Number.POSITIVE_INFINITY;
        const maxTop = hasViewport ? Math.max(VIEWPORT_MARGIN_PX, vpH - height - VIEWPORT_MARGIN_PX) : Number.POSITIVE_INFINITY;

        const left = clamp(win.x, minLeft, maxLeft);
        const top = clamp(win.y, minTop, maxTop);

        const dx = dragState?._lastX !== undefined ? dragState._lastX - left : 0;
        const dy = dragState?._lastY !== undefined ? dragState._lastY - top : 0;
        const transform = isDragging === win.id ? `translate3d(${dx}px, ${dy}px, 0)` : undefined;
        const zIndex = 100 + Math.max(0, zOrder.indexOf(win.id));
        const isFocused = focusedWindowId === win.id;

        return (
          <div
            key={win.id}
            ref={(el) => setWindowRef(win.id, el)}
            onMouseDownCapture={() => bringToFront(win.id)}
            className={`fixed z-[100] bg-stone-900 border border-stone-700 rounded-lg shadow-2xl flex flex-col overflow-hidden ring-1 ring-white/5 ${isFocused ? 'ring-2 ring-sage-500/40' : ''} ${isMoving ? '' : 'transition-all duration-200'}
              ${isDropTarget ? 'console-drop-target-active' : ''}
              ${isBeingDraggedOverTarget ? 'console-window-dragging-over-target' : ''}`}
            style={{
              left,
              top,
              width,
              height,
              zIndex,
              transform,
              willChange: isMoving ? 'transform, width, height' : 'auto',
              boxShadow:
                isDragging === win.id || isResizing === win.id
                  ? '0 25px 50px -12px rgba(0, 0, 0, 0.7)'
                  : '0 20px 25px -5px rgba(0, 0, 0, 0.4)',
            }}
          >
            <div
              className="h-9 bg-stone-800 border-b border-stone-700 flex items-center cursor-move select-none shrink-0"
              onMouseDown={(e) => handleMouseDown(e, win, left, top)}
            >
              <div className="flex-1 flex items-center h-full overflow-x-auto no-scrollbar scroll-smooth">
                {win.deviceIds.map((nodeId, index) => {
                  const node = nodes.find((n) => n.id === nodeId);
                  const isActive = win.activeDeviceId === nodeId;
                  const isTabBeingDragged = tabDragState?.isDragging &&
                    tabDragState.windowId === win.id &&
                    tabDragState.deviceId === nodeId;
                  const isTabBeingReordered = tabDragState?.isReordering &&
                    tabDragState.windowId === win.id &&
                    tabDragState.deviceId === nodeId;

                  // Show reorder indicator before this tab if target index matches
                  const showIndicatorBefore = tabDragState?.isReordering &&
                    tabDragState.windowId === win.id &&
                    tabDragState.reorderTargetIndex === index;

                  return (
                    <React.Fragment key={nodeId}>
                      {/* Reorder drop indicator before this tab */}
                      {showIndicatorBefore && (
                        <div className="console-tab-reorder-indicator" />
                      )}
                      <div
                        ref={(el) => {
                          if (el) {
                            tabRefs.current.set(`${win.id}-${nodeId}`, el);
                          } else {
                            tabRefs.current.delete(`${win.id}-${nodeId}`);
                          }
                        }}
                        onMouseDown={(e) => handleTabMouseDown(e, win, nodeId)}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (!tabDragState?.isDragging && !tabDragState?.isReordering) {
                            onSetActiveTab(win.id, nodeId);
                          }
                        }}
                        className={`h-full px-4 flex items-center gap-2 text-[10px] font-bold border-r border-stone-700/50 transition-all cursor-pointer shrink-0 relative
                          ${isActive ? 'bg-stone-900 text-sage-400' : 'text-stone-500 hover:bg-stone-700/50 hover:text-stone-300'}
                          ${isTabBeingDragged ? 'console-tab-dragging' : ''}
                          ${isTabBeingReordered ? 'console-tab-reordering' : ''}
                          ${win.deviceIds.length > 1 ? 'cursor-grab active:cursor-grabbing' : ''}`}
                      >
                        {isActive && <div className="absolute top-0 left-0 right-0 h-0.5 bg-sage-500" />}
                        <i className={`fa-solid ${isActive ? 'fa-terminal' : 'fa-rectangle-list'} scale-90`}></i>
                        <span className="truncate max-w-[80px]">{node?.name || 'Unknown'}</span>
                        <button
                          onMouseDown={(e) => e.stopPropagation()}
                          onClick={(e) => {
                            e.stopPropagation();
                            onCloseTab(win.id, nodeId);
                          }}
                          className="ml-1 hover:text-red-400 p-0.5 transition-colors opacity-60 hover:opacity-100"
                        >
                          <i className="fa-solid fa-xmark"></i>
                        </button>
                      </div>
                    </React.Fragment>
                  );
                })}
                {/* Reorder drop indicator at the end */}
                {tabDragState?.isReordering &&
                  tabDragState.windowId === win.id &&
                  tabDragState.reorderTargetIndex === win.deviceIds.length && (
                  <div className="console-tab-reorder-indicator" />
                )}
              </div>
              <div className="flex items-center px-2 gap-1.5 shrink-0 bg-stone-800 ml-auto border-l border-stone-700">
                {!isMinimized && onDockWindow && (
                  <button
                    className="w-6 h-6 flex items-center justify-center text-stone-500 hover:text-sage-400 hover:bg-stone-700 rounded transition-all"
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={() => onDockWindow(win.id)}
                    title="Dock to bottom panel"
                  >
                    <i className="fa-solid fa-window-restore text-[9px]"></i>
                  </button>
                )}
                {!isMinimized && (
                  <button
                    className="w-6 h-6 flex items-center justify-center text-stone-500 hover:text-stone-300 hover:bg-stone-700 rounded transition-all"
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={() => {
                      if (!activeNode) return;
                      const url = `/studio/console/${encodeURIComponent(labId)}/${encodeURIComponent(activeNode.id)}`;
                      window.open(url, `archetype-console-${activeNode.id}`, 'width=960,height=640');
                    }}
                    title="Open in new window"
                  >
                    <i className="fa-solid fa-up-right-from-square text-[9px]"></i>
                  </button>
                )}
                {onToggleMinimize && (
                  <button
                    className="w-6 h-6 flex items-center justify-center text-stone-500 hover:text-stone-300 hover:bg-stone-700 rounded transition-all"
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={() => onToggleMinimize(win.id)}
                  >
                    <i className={`fa-solid ${isMinimized ? 'fa-window-maximize' : 'fa-window-minimize'} text-[9px]`}></i>
                  </button>
                )}
                <button
                  onClick={() => onCloseWindow(win.id)}
                  onMouseDown={(e) => e.stopPropagation()}
                  className="w-6 h-6 flex items-center justify-center text-stone-500 hover:text-red-400 hover:bg-red-400/10 rounded transition-all"
                >
                  <i className="fa-solid fa-xmark"></i>
                </button>
              </div>
            </div>

            {!isMinimized && (
              <>
                <div className="flex-1 bg-[#0b0f16] relative">
                  {win.deviceIds.length === 0 && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center text-stone-700">
                      <i className="fa-solid fa-terminal text-4xl mb-4 opacity-10"></i>
                      <p className="text-xs font-bold uppercase tracking-widest opacity-30">No active session selected</p>
                    </div>
                  )}
                  {win.deviceIds.map((nodeId) => {
                    const nodeState = nodeStates[nodeId];
                    // Only show boot warning for running nodes that aren't ready yet
                    // For error/stopped/pending states, don't show boot warning
                    const isRunning = nodeState?.actual_state === 'running';
                    const isReady = !isRunning || nodeState?.is_ready !== false;
                    return (
                      <div
                        key={nodeId}
                        className={`absolute inset-0 ${win.activeDeviceId === nodeId ? 'block' : 'hidden'}`}
                      >
                        <TerminalSession
                          labId={labId}
                          nodeId={nodeId}
                          isActive={showConsoles && win.activeDeviceId === nodeId}
                          isReady={isReady}
                        />
                      </div>
                    );
                  })}
                </div>

                <div
                  className="absolute bottom-0 right-0 w-5 h-5 cursor-nwse-resize flex items-end justify-end p-0.5 group pointer-events-auto"
                  onMouseDown={(e) => handleResizeMouseDown(e, win, left, top, width, height)}
                >
                  <div className="w-2 h-2 border-r-2 border-b-2 border-stone-700 group-hover:border-sage-500 transition-colors"></div>
                </div>
              </>
            )}
          </div>
        );
      })}

      {/* Tab ghost preview that follows cursor during tab drag */}
      {tabDragNode && tabDragState?.isDragging && (
        <div
          className="console-tab-ghost"
          style={{
            left: tabDragState.currentX + 10,
            top: tabDragState.currentY + 10,
          }}
        >
          <i className="fa-solid fa-terminal"></i>
          <span>{tabDragNode.name}</span>
        </div>
      )}

      {/* Dock zone overlay at bottom of screen */}
      {showDockZone && isDragging && (
        <div
          className="fixed left-0 right-0 z-[99] flex items-center justify-center bg-sage-500/20 border-t-2 border-sage-500 backdrop-blur-sm transition-all animate-pulse"
          style={{
            bottom: 0,
            height: DOCK_ZONE_HEIGHT,
          }}
        >
          <div className="flex items-center gap-3 px-6 py-3 bg-sage-600/90 rounded-lg shadow-lg text-white font-bold text-sm">
            <i className="fa-solid fa-chevron-down"></i>
            <span>Drop to dock in bottom panel</span>
            <i className="fa-solid fa-chevron-down"></i>
          </div>
        </div>
      )}
    </>
  );
};

export default React.memo(ConsoleManager);
