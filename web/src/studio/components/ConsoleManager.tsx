import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ConsoleWindow, Node } from '../types';
import TerminalSession from './TerminalSession';
import { NodeStateEntry } from '../../types/nodeState';

interface ConsoleManagerProps {
  labId: string;
  windows: ConsoleWindow[];
  nodes: Node[];
  nodeStates?: Record<string, NodeStateEntry>;
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
  // Ref-based drag/resize tracking (no re-renders during movement)
  const dragRef = useRef<{ id: string; startX: number; startY: number; originX: number; originY: number } | null>(null);
  const resizeRef = useRef<{ id: string; startWidth: number; startHeight: number; startX: number; startY: number } | null>(null);
  const dragPerfRef = useRef<{ enabled: boolean; start: number; lastSample: number; frames: number } | null>(null);
  const dragProfileRef = useRef<{
    enabled: boolean;
    lastFrameTs: number;
    frameIntervals: number[];
    longTasksTotal: number;
    longTasksCount: number;
    observer?: PerformanceObserver;
  } | null>(null);

  // Boolean state for CSS shadow (only changes on start/end)
  const [isDragging, setIsDragging] = useState<string | null>(null);
  const [isResizing, setIsResizing] = useState<string | null>(null);
  const [profileEnabled, setProfileEnabled] = useState(() => localStorage.getItem('consoleDragProfile') === '1');

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

  const handleMouseDown = (e: React.MouseEvent, win: ConsoleWindow) => {
    dragRef.current = {
      id: win.id,
      startX: e.clientX - win.x,
      startY: e.clientY - win.y,
      originX: win.x,
      originY: win.y,
    };
    const perfEnabled = localStorage.getItem('consoleDragPerf') === '1';
    dragPerfRef.current = perfEnabled
      ? { enabled: true, start: performance.now(), lastSample: performance.now(), frames: 0 }
      : { enabled: false, start: 0, lastSample: 0, frames: 0 };
    const profileEnabled = localStorage.getItem('consoleDragProfile') === '1';
    if (profileEnabled) {
      const profileState = {
        enabled: true,
        lastFrameTs: performance.now(),
        frameIntervals: [] as number[],
        longTasksTotal: 0,
        longTasksCount: 0,
        observer: undefined as PerformanceObserver | undefined,
      };
      if (typeof PerformanceObserver !== 'undefined') {
        try {
          const observer = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              profileState.longTasksTotal += entry.duration;
              profileState.longTasksCount += 1;
            }
          });
          observer.observe({ entryTypes: ['longtask'] });
          profileState.observer = observer;
        } catch {
          // Ignore observer setup failures (not supported in some browsers)
        }
      }
      dragProfileRef.current = profileState;
    } else {
      dragProfileRef.current = { enabled: false, lastFrameTs: 0, frameIntervals: [], longTasksTotal: 0, longTasksCount: 0 };
    }
    setIsDragging(win.id);
  };

  const toggleDragProfile = useCallback(() => {
    setProfileEnabled((prev) => {
      const next = !prev;
      if (next) {
        localStorage.setItem('consoleDragProfile', '1');
      } else {
        localStorage.removeItem('consoleDragProfile');
      }
      return next;
    });
  }, []);

  const handleResizeMouseDown = (e: React.MouseEvent, win: ConsoleWindow) => {
    e.stopPropagation();
    e.preventDefault();
    const currentW = winSizes[win.id]?.w || 520;
    const currentH = winSizes[win.id]?.h || 360;
    resizeRef.current = {
      id: win.id,
      startWidth: currentW,
      startHeight: currentH,
      startX: e.clientX,
      startY: e.clientY,
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

          if (dragProfileRef.current?.enabled) {
            const now = performance.now();
            const interval = now - dragProfileRef.current.lastFrameTs;
            dragProfileRef.current.frameIntervals.push(interval);
            dragProfileRef.current.lastFrameTs = now;
          }
        }

        // Handle window resize — direct DOM update
        if (resizeRef.current) {
          const deltaX = e.clientX - resizeRef.current.startX;
          const deltaY = e.clientY - resizeRef.current.startY;
          const newW = Math.max(320, resizeRef.current.startWidth + deltaX);
          const newH = Math.max(240, resizeRef.current.startHeight + deltaY);

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
        const lastX = (dragRef.current as any)._lastX;
        const lastY = (dragRef.current as any)._lastY;
        if (lastX !== undefined && lastY !== undefined) {
          onUpdateWindowPosRef.current(dragRef.current.id, lastX, lastY);
        }
        const el = windowRefs.current.get(dragRef.current.id);
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
      if (dragProfileRef.current?.enabled) {
        const { frameIntervals, longTasksCount, longTasksTotal, observer } = dragProfileRef.current;
        if (observer) observer.disconnect();
        if (frameIntervals.length > 1) {
          const sorted = [...frameIntervals].sort((a, b) => a - b);
          const avg = frameIntervals.reduce((sum, v) => sum + v, 0) / frameIntervals.length;
          const p95 = sorted[Math.floor(sorted.length * 0.95)] || 0;
          const over16 = frameIntervals.filter((v) => v > 16.7).length;
          const over33 = frameIntervals.filter((v) => v > 33.3).length;
          const over50 = frameIntervals.filter((v) => v > 50).length;
          console.groupCollapsed('[console-drag-profile]');
          console.table({
            frames: frameIntervals.length,
            avgMs: Number(avg.toFixed(2)),
            p95Ms: Number(p95.toFixed(2)),
            over16ms: over16,
            over33ms: over33,
            over50ms: over50,
            longTasks: longTasksCount,
            longTasksMs: Number(longTasksTotal.toFixed(2)),
          });
          console.groupEnd();
        }
      }
      dragProfileRef.current = null;
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
        const width = resizeState?._lastW ?? (isMinimized ? 280 : size.w);
        const height = resizeState?._lastH ?? (isMinimized ? 36 : size.h);
        const dx = dragState?._lastX !== undefined ? dragState._lastX - win.x : 0;
        const dy = dragState?._lastY !== undefined ? dragState._lastY - win.y : 0;
        const transform = isDragging === win.id ? `translate3d(${dx}px, ${dy}px, 0)` : undefined;

        return (
          <div
            key={win.id}
            ref={(el) => setWindowRef(win.id, el)}
            className={`fixed z-[100] bg-stone-900 border border-stone-700 rounded-lg shadow-2xl flex flex-col overflow-hidden ring-1 ring-white/5 ${isMoving ? '' : 'transition-all duration-200'}
              ${isDropTarget ? 'console-drop-target-active' : ''}
              ${isBeingDraggedOverTarget ? 'console-window-dragging-over-target' : ''}`}
            style={{
              left: win.x,
              top: win.y,
              width,
              height,
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
              onMouseDown={(e) => handleMouseDown(e, win)}
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
                <button
                  className={`w-8 h-6 flex items-center justify-center text-[9px] font-bold uppercase tracking-widest rounded transition-all ${
                    profileEnabled
                      ? 'text-sage-300 bg-sage-500/20 hover:bg-sage-500/30'
                      : 'text-stone-500 hover:text-stone-300 hover:bg-stone-700'
                  }`}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={toggleDragProfile}
                  title="Toggle drag profiling (consoleDragProfile)"
                >
                  Perf
                </button>
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
                          isActive={win.activeDeviceId === nodeId}
                          isReady={isReady}
                        />
                      </div>
                    );
                  })}
                </div>

                <div
                  className="absolute bottom-0 right-0 w-5 h-5 cursor-nwse-resize flex items-end justify-end p-0.5 group pointer-events-auto"
                  onMouseDown={(e) => handleResizeMouseDown(e, win)}
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
