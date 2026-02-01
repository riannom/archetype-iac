import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ConsoleWindow, Node } from '../types';
import TerminalSession from './TerminalSession';

interface NodeStateEntry {
  id: string;
  node_id: string;
  actual_state: string;
  is_ready?: boolean;
}

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
}

// Threshold in pixels before a tab drag initiates a split
const TAB_DRAG_THRESHOLD = 30;
// Threshold for horizontal movement to trigger reorder mode
const REORDER_THRESHOLD = 10;

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
}) => {
  const [dragState, setDragState] = useState<{ id: string; startX: number; startY: number } | null>(null);
  const [resizeState, setResizeState] = useState<{ id: string; startWidth: number; startHeight: number; startX: number; startY: number } | null>(null);
  const [winSizes, setWinSizes] = useState<Record<string, { w: number; h: number }>>({});

  // Drop target detection state
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
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
    setDragState({ id: win.id, startX: e.clientX - win.x, startY: e.clientY - win.y });
  };

  const handleResizeMouseDown = (e: React.MouseEvent, win: ConsoleWindow) => {
    e.stopPropagation();
    e.preventDefault();
    const currentW = winSizes[win.id]?.w || 520;
    const currentH = winSizes[win.id]?.h || 360;
    setResizeState({
      id: win.id,
      startWidth: currentW,
      startHeight: currentH,
      startX: e.clientX,
      startY: e.clientY,
    });
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
  const calculateReorderIndex = useCallback((clientX: number, windowId: string, draggedId: string): number => {
    const win = windows.find(w => w.id === windowId);
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
  }, [windows]);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      // Handle window drag
      if (dragState) {
        onUpdateWindowPos(dragState.id, e.clientX - dragState.startX, e.clientY - dragState.startY);

        // Check for drop target (for window merge)
        if (onMergeWindows) {
          const target = findDropTarget(e.clientX, e.clientY, dragState.id);
          setDropTargetId(target);
        }
      }

      // Handle window resize
      if (resizeState) {
        const deltaX = e.clientX - resizeState.startX;
        const deltaY = e.clientY - resizeState.startY;
        setWinSizes((prev) => ({
          ...prev,
          [resizeState.id]: {
            w: Math.max(320, resizeState.startWidth + deltaX),
            h: Math.max(240, resizeState.startHeight + deltaY),
          },
        }));
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
            const targetIndex = calculateReorderIndex(e.clientX, prev.windowId, prev.deviceId);
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
            const targetIndex = calculateReorderIndex(e.clientX, prev.windowId, prev.deviceId);
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
    };

    const handleMouseUp = (e: MouseEvent) => {
      // Handle window merge on drop
      if (dragState && dropTargetId && onMergeWindows) {
        onMergeWindows(dragState.id, dropTargetId);
      }

      // Handle tab reorder on drop
      if (tabDragState?.isReordering && tabDragState.reorderTargetIndex !== null && onReorderTab) {
        const win = windows.find(w => w.id === tabDragState.windowId);
        if (win) {
          const fromIndex = win.deviceIds.indexOf(tabDragState.deviceId);
          const toIndex = tabDragState.reorderTargetIndex;
          if (fromIndex !== -1 && fromIndex !== toIndex && fromIndex !== toIndex - 1) {
            onReorderTab(tabDragState.windowId, fromIndex, toIndex);
          }
        }
      }
      // Handle tab split on drop
      else if (tabDragState?.isDragging && onSplitTab) {
        onSplitTab(tabDragState.windowId, tabDragState.deviceId, e.clientX - 260, e.clientY - 50);
      }

      setDragState(null);
      setResizeState(null);
      setDropTargetId(null);
      setTabDragState(null);
    };

    if (dragState || resizeState || tabDragState) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    }
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [dragState, resizeState, tabDragState, dropTargetId, windows, onUpdateWindowPos, onMergeWindows, onSplitTab, onReorderTab, findDropTarget, calculateReorderIndex]);

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
        const isBeingDraggedOverTarget = dragState?.id === win.id && dropTargetId !== null;

        return (
          <div
            key={win.id}
            ref={(el) => setWindowRef(win.id, el)}
            className={`fixed z-[100] bg-stone-900 border border-stone-700 rounded-lg shadow-2xl flex flex-col overflow-hidden ring-1 ring-white/5
              ${isDropTarget ? 'console-drop-target-active' : ''}
              ${isBeingDraggedOverTarget ? 'console-window-dragging-over-target' : ''}`}
            style={{
              left: win.x,
              top: win.y,
              width: size.w,
              height: size.h,
              boxShadow:
                dragState?.id === win.id || resizeState?.id === win.id
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
                  className="w-6 h-6 flex items-center justify-center text-stone-500 hover:text-stone-300 hover:bg-stone-700 rounded transition-all"
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={() => {
                    if (!activeNode) return;
                    const url = `/studio/console/${encodeURIComponent(labId)}/${encodeURIComponent(activeNode.id)}`;
                    window.open(url, `archetype-console-${activeNode.id}`, 'width=960,height=640');
                  }}
                >
                  <i className="fa-solid fa-up-right-from-square text-[9px]"></i>
                </button>
                <button
                  onClick={() => onCloseWindow(win.id)}
                  className="w-6 h-6 flex items-center justify-center text-stone-500 hover:text-red-400 hover:bg-red-400/10 rounded transition-all"
                >
                  <i className="fa-solid fa-xmark"></i>
                </button>
              </div>
            </div>

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
    </>
  );
};

export default ConsoleManager;
