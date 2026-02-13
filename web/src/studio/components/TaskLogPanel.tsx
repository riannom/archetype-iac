import React, { useState, useCallback, useEffect, useRef } from 'react';
import TerminalSession from './TerminalSession';
import { NodeStateEntry } from '../../types/nodeState';
import { useTheme } from '../../theme';

export interface TaskLogEntry {
  id: string;
  timestamp: Date;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  jobId?: string;
}

export interface DockedConsole {
  nodeId: string;
  nodeName: string;
}

interface TaskLogPanelProps {
  entries: TaskLogEntry[];
  isVisible: boolean;
  onToggle: () => void;
  onClear: () => void;
  autoUpdateEnabled?: boolean;
  onToggleAutoUpdate?: (enabled: boolean) => void;
  onEntryClick?: (entry: TaskLogEntry) => void;
  showConsoles?: boolean;
  // Console tabs support
  consoleTabs?: DockedConsole[];
  activeTabId?: string; // 'log' | nodeId
  onSelectTab?: (tabId: string) => void;
  onCloseConsoleTab?: (nodeId: string) => void;
  onUndockConsole?: (nodeId: string, x: number, y: number) => void;
  onReorderTab?: (fromIndex: number, toIndex: number) => void;
  // Lab context for terminals
  labId?: string;
  nodeStates?: Record<string, NodeStateEntry>;
}

const MIN_HEIGHT = 100;
const MAX_HEIGHT = 600;
const DEFAULT_HEIGHT = 200;
const STORAGE_KEY = 'archetype-tasklog-height';
const TAB_UNDOCK_THRESHOLD = 30; // pixels of vertical drag to trigger undock
const TAB_REORDER_THRESHOLD = 10; // pixels of horizontal drag to trigger reorder
const TAB_STRIP_OUTSIDE_PX = 6;

const TaskLogPanel: React.FC<TaskLogPanelProps> = ({
  entries,
  isVisible,
  onToggle,
  onClear,
  autoUpdateEnabled = true,
  onToggleAutoUpdate,
  onEntryClick,
  showConsoles = true,
  consoleTabs = [],
  activeTabId = 'log',
  onSelectTab,
  onCloseConsoleTab,
  onUndockConsole,
  onReorderTab,
  labId,
  nodeStates = {},
}) => {
  const { effectiveMode } = useTheme();
  const errorCount = entries.filter((e) => e.level === 'error').length;
  const hasConsoleTabs = showConsoles && consoleTabs.length > 0;

  const [height, setHeight] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? Math.min(Math.max(parseInt(saved, 10), MIN_HEIGHT), MAX_HEIGHT) : DEFAULT_HEIGHT;
  });
  const [isResizing, setIsResizing] = useState(false);
  const startY = useRef(0);
  const startHeight = useRef(0);
  const logContainerRef = useRef<HTMLDivElement | null>(null);

  // Tab drag state for undocking and reordering
  const [tabDragState, setTabDragState] = useState<{
    nodeId: string;
    startX: number;
    startY: number;
    currentX: number;
    currentY: number;
    isDragging: boolean; // undock active
    isReordering: boolean; // horizontal drag for reorder
    reorderTargetIndex: number | null;
    tabStripRect: { left: number; top: number; right: number; bottom: number } | null;
  } | null>(null);

  // Refs to track tab elements for calculating drop positions
  const tabRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
    startY.current = e.clientY;
    startHeight.current = height;
  }, [height]);

  // Handle tab mousedown for potential undock drag or reorder
  const handleTabMouseDown = useCallback((e: React.MouseEvent, nodeId: string) => {
    if (!onUndockConsole && !onReorderTab) return;
    e.stopPropagation();
    const tabStripEl = (e.currentTarget as HTMLElement | null)?.parentElement as HTMLElement | null;
    const tabStripRect = tabStripEl?.getBoundingClientRect?.();
    setTabDragState({
      nodeId,
      startX: e.clientX,
      startY: e.clientY,
      currentX: e.clientX,
      currentY: e.clientY,
      isDragging: false,
      isReordering: false,
      reorderTargetIndex: null,
      tabStripRect: tabStripRect
        ? { left: tabStripRect.left, top: tabStripRect.top, right: tabStripRect.right, bottom: tabStripRect.bottom }
        : null,
    });
  }, [onUndockConsole, onReorderTab]);

  // Calculate the target index for reordering based on cursor position
  const calculateReorderIndex = useCallback((clientX: number, draggedNodeId: string): number => {
    let targetIndex = 0;
    for (let i = 0; i < consoleTabs.length; i++) {
      const tabEl = tabRefs.current.get(consoleTabs[i].nodeId);
      if (!tabEl) continue;
      const rect = tabEl.getBoundingClientRect();
      const midpoint = rect.left + rect.width / 2;
      if (clientX > midpoint) targetIndex = i + 1;
    }
    return targetIndex;
  }, [consoleTabs]);

  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      const delta = startY.current - e.clientY;
      const newHeight = Math.min(Math.max(startHeight.current + delta, MIN_HEIGHT), MAX_HEIGHT);
      setHeight(newHeight);
      localStorage.setItem(STORAGE_KEY, newHeight.toString());
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    // Prevent text selection while resizing
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'ns-resize';

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing]);

  // Handle tab drag for undocking and reordering
  useEffect(() => {
    if (!tabDragState) return;

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = e.clientX - tabDragState.startX;
      const deltaY = e.clientY - tabDragState.startY;
      const absX = Math.abs(deltaX);
      const absY = Math.abs(deltaY);
      const distance = Math.hypot(deltaX, deltaY);

      setTabDragState(prev => {
        if (!prev) return null;

        const undockAllowed = !!onUndockConsole;
        const reorderAllowed = !!onReorderTab;
        // Undock should work in any direction once the cursor leaves the tab strip.
        // Reordering should only happen while the cursor stays within the tab strip.
        const r = prev.tabStripRect;
        const isOutsideTabStrip = r
          ? (
            e.clientX < r.left - TAB_STRIP_OUTSIDE_PX ||
            e.clientX > r.right + TAB_STRIP_OUTSIDE_PX ||
            e.clientY < r.top - TAB_STRIP_OUTSIDE_PX ||
            e.clientY > r.bottom + TAB_STRIP_OUTSIDE_PX
          )
          : absY >= TAB_UNDOCK_THRESHOLD; // Fallback if rect isn't available

        const wantsUndock = undockAllowed && distance >= TAB_UNDOCK_THRESHOLD && isOutsideTabStrip;
        const wantsReorder = reorderAllowed && absX >= TAB_REORDER_THRESHOLD && !isOutsideTabStrip;

        // Undock overrides reorder if the user drags away from the tab strip.
        if (wantsUndock) {
          return {
            ...prev,
            currentX: e.clientX,
            currentY: e.clientY,
            isDragging: true,
            isReordering: false,
            reorderTargetIndex: null,
          };
        }

        // If already in undock mode, continue tracking
        if (prev.isDragging) {
          return { ...prev, currentX: e.clientX, currentY: e.clientY };
        }

        // Horizontal-dominant movement → reorder mode
        if (wantsReorder && !prev.isDragging) {
          const targetIndex = calculateReorderIndex(e.clientX, prev.nodeId);
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
          const targetIndex = calculateReorderIndex(e.clientX, prev.nodeId);
          return {
            ...prev,
            currentX: e.clientX,
            currentY: e.clientY,
            reorderTargetIndex: targetIndex,
          };
        }

        // Not enough movement yet
        return { ...prev, currentX: e.clientX, currentY: e.clientY };
      });
    };

    const handleMouseUp = (e: MouseEvent) => {
      // Handle reorder on drop
      if (tabDragState.isReordering && tabDragState.reorderTargetIndex !== null && onReorderTab) {
        const fromIndex = consoleTabs.findIndex(t => t.nodeId === tabDragState.nodeId);
        const toIndex = tabDragState.reorderTargetIndex;
        if (fromIndex !== -1 && fromIndex !== toIndex && fromIndex !== toIndex - 1) {
          onReorderTab(fromIndex, toIndex);
        }
      }
      // Handle undock on drop
      else if (tabDragState.isDragging && onUndockConsole) {
        onUndockConsole(tabDragState.nodeId, e.clientX - 260, e.clientY - 50);
      }
      setTabDragState(null);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [tabDragState, onUndockConsole, onReorderTab, consoleTabs, calculateReorderIndex]);

  const levelColors = {
    info: 'text-cyan-700 dark:text-cyan-400',
    success: 'text-green-700 dark:text-green-400',
    warning: 'text-amber-700 dark:text-yellow-400',
    error: 'text-red-700 dark:text-red-400',
  };

  const levelBorders = {
    info: 'border-l-cyan-500',
    success: 'border-l-green-500',
    warning: 'border-l-amber-500 dark:border-l-yellow-500',
    error: 'border-l-red-500 bg-red-100/50 dark:bg-red-900/20',
  };

  const isLogTabActive = activeTabId === 'log';
  const logTabActive = showConsoles ? isLogTabActive : true;
  const showLogContent = isLogTabActive || !showConsoles;
  const headerBarStyle: React.CSSProperties = {
    backgroundColor:
      effectiveMode === 'light'
        ? 'var(--color-accent-600)'
        : 'color-mix(in srgb, var(--color-bg-surface) 92%, transparent)',
  };
  const headerTitleClass = effectiveMode === 'light' ? 'text-white/90' : 'text-stone-600 dark:text-stone-400';
  const headerActionClass = effectiveMode === 'light'
    ? 'text-white/80 hover:text-white'
    : 'text-stone-500 hover:text-stone-700 dark:hover:text-stone-300';
  const headerChevronClass = effectiveMode === 'light'
    ? 'text-white/75 hover:text-white'
    : 'text-stone-400 dark:text-stone-500 hover:text-stone-600 dark:hover:text-stone-300';
  const headerHoverClass = effectiveMode === 'light' ? 'hover:bg-white/10' : 'hover:bg-stone-100/50 dark:hover:bg-stone-900/50';
  const gripClass = effectiveMode === 'light' ? 'bg-white/40 group-hover:bg-white/60' : 'bg-stone-300 dark:bg-stone-600 group-hover:bg-cyan-400';

  // Keep task log pinned to the latest entry when auto-refresh is enabled.
  useEffect(() => {
    if (!isVisible || !showLogContent || !autoUpdateEnabled) return;
    const el = logContainerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [entries, autoUpdateEnabled, isVisible, showLogContent]);

  return (
    <div
      className="shrink-0 backdrop-blur-md border-t border-stone-200 dark:border-stone-800"
      style={{ backgroundColor: 'rgb(var(--tasklog-panel-bg) / var(--tasklog-opacity, 0.92))' }}
    >
      {/* Header with integrated resize handle at top */}
      <div
        className={`flex justify-between items-center px-4 py-2 select-none ${
          isVisible ? 'cursor-ns-resize' : 'cursor-pointer'
        } ${headerHoverClass} group relative`}
        style={headerBarStyle}
        onMouseDown={isVisible ? handleMouseDown : undefined}
        onClick={isVisible ? undefined : onToggle}
      >
        {/* Resize grip indicator - only when expanded */}
        {isVisible && (
          <div className="absolute top-0 left-0 right-0 h-1 flex items-center justify-center">
            <div className={`w-10 h-1 rounded-full transition-colors ${
              isResizing ? 'bg-cyan-500' : gripClass
            }`} />
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-black uppercase tracking-widest ${headerTitleClass}`}>
            {hasConsoleTabs ? 'Panel' : 'Task Log'}
          </span>
          {errorCount > 0 && logTabActive && (
            <span className="px-1.5 py-0.5 bg-red-600 text-white text-[9px] font-bold rounded-full">
              {errorCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {isVisible && logTabActive && onToggleAutoUpdate && (
            <label
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
              className={`flex items-center gap-1.5 cursor-pointer text-[10px] font-bold uppercase tracking-widest ${headerActionClass}`}
            >
              <input
                type="checkbox"
                checked={autoUpdateEnabled}
                onChange={(e) => onToggleAutoUpdate(e.target.checked)}
                className="w-3 h-3 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
              />
              <span>Auto-refresh</span>
            </label>
          )}
          {isVisible && logTabActive && (
            <button
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onClear();
              }}
              className={`text-[10px] font-bold uppercase tracking-widest ${headerActionClass}`}
            >
              Clear
            </button>
          )}
          <button
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }}
            className={`text-xs px-1 ${headerChevronClass}`}
          >
            {isVisible ? 'v' : '^'}
          </button>
        </div>
      </div>

      {isVisible && (
        <>
          {/* Tab bar - only show when there are console tabs */}
          {hasConsoleTabs && (
            <div className="flex items-center border-b border-stone-200 dark:border-stone-800 bg-transparent px-2">
              {/* Log tab */}
              <button
                onClick={() => onSelectTab?.('log')}
                className={`h-8 px-4 flex items-center gap-2 text-[10px] font-bold border-b-2 transition-all ${
                  logTabActive
                    ? 'text-sage-600 dark:text-sage-400 border-sage-500'
                    : 'text-stone-500 dark:text-stone-500 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
                }`}
              >
                <i className="fa-solid fa-list-check text-[9px]"></i>
                <span>Log</span>
                {errorCount > 0 && (
                  <span className="px-1.5 py-0.5 bg-red-600 text-white text-[8px] font-bold rounded-full">
                    {errorCount}
                  </span>
                )}
              </button>

              {/* Console tabs */}
              {showConsoles && consoleTabs.map((tab, index) => {
                const isActive = activeTabId === tab.nodeId;
                const isBeingDragged = tabDragState?.nodeId === tab.nodeId && tabDragState.isDragging;
                const isBeingReordered = tabDragState?.nodeId === tab.nodeId && tabDragState.isReordering;

                // Show reorder indicator before this tab if target index matches
                const showIndicatorBefore = tabDragState?.isReordering &&
                  tabDragState.reorderTargetIndex === index;

                return (
                  <React.Fragment key={tab.nodeId}>
                    {/* Reorder drop indicator before this tab */}
                    {showIndicatorBefore && (
                      <div className="console-tab-reorder-indicator" />
                    )}
                    <div
                      ref={(el) => {
                        if (el) {
                          tabRefs.current.set(tab.nodeId, el);
                        } else {
                          tabRefs.current.delete(tab.nodeId);
                        }
                      }}
                      onMouseDown={(e) => handleTabMouseDown(e, tab.nodeId)}
                      onClick={() => {
                        if (!tabDragState?.isDragging && !tabDragState?.isReordering) {
                          onSelectTab?.(tab.nodeId);
                        }
                      }}
                      className={`h-8 px-4 flex items-center gap-2 text-[10px] font-bold border-b-2 transition-all cursor-pointer shrink-0 ${
                        isActive
                          ? 'text-sage-600 dark:text-sage-400 border-sage-500'
                          : 'text-stone-500 dark:text-stone-500 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
                      } ${isBeingDragged ? 'opacity-50' : ''} ${isBeingReordered ? 'opacity-50 bg-sage-500/10' : ''} ${onUndockConsole || onReorderTab ? 'cursor-grab active:cursor-grabbing' : ''}`}
                    >
                      <i className="fa-solid fa-terminal text-[9px]"></i>
                      <span className="truncate max-w-[100px]">{tab.nodeName}</span>
                      {onCloseConsoleTab && (
                        <button
                          onMouseDown={(e) => e.stopPropagation()}
                          onClick={(e) => {
                            e.stopPropagation();
                            onCloseConsoleTab(tab.nodeId);
                          }}
                          className="ml-1 hover:text-red-400 p-0.5 transition-colors opacity-60 hover:opacity-100"
                        >
                          <i className="fa-solid fa-xmark text-[8px]"></i>
                        </button>
                      )}
                    </div>
                  </React.Fragment>
                );
              })}
              {/* Reorder drop indicator at the end */}
              {tabDragState?.isReordering &&
                tabDragState.reorderTargetIndex === consoleTabs.length && (
                <div className="console-tab-reorder-indicator" />
              )}
            </div>
          )}

          {/* Content area */}
          <div
            className="overflow-hidden"
            style={{ height: `${height}px` }}
          >
            {/* Log content */}
            {showLogContent && (
              <div ref={logContainerRef} className="h-full overflow-y-auto font-mono text-[11px]">
                {entries.length === 0 ? (
                  <div className="px-4 py-6 text-center text-stone-400 dark:text-stone-600">No task activity yet</div>
                ) : (
                  entries.map((entry) => {
                    const isClickable = !!onEntryClick;
                    return (
                      <div
                        key={entry.id}
                        onClick={isClickable ? () => onEntryClick(entry) : undefined}
                        className={`flex gap-3 px-4 py-1.5 border-l-2 ${levelBorders[entry.level]} ${
                          isClickable ? 'cursor-pointer hover:bg-stone-100 dark:hover:bg-stone-800/50' : ''
                        }`}
                      >
                        <span className="text-stone-400 dark:text-stone-600 min-w-[70px]">
                          {entry.timestamp.toLocaleTimeString()}
                        </span>
                        <span className={`min-w-[50px] font-bold uppercase ${levelColors[entry.level]}`}>
                          {entry.level}
                        </span>
                        <span className="text-stone-700 dark:text-stone-300 flex-1">{entry.message}</span>
                        {isClickable && (
                          <span className="text-stone-400 dark:text-stone-600 text-[10px] self-center">
                            <i className="fa-solid fa-chevron-right" />
                          </span>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            )}

            {/* Console content */}
            {!isLogTabActive && labId && (
              <div className={showConsoles ? '' : 'hidden'}>
                {consoleTabs.map((tab) => {
                  if (activeTabId !== tab.nodeId) return null;
                  const nodeState = nodeStates[tab.nodeId];
                  const isRunning = nodeState?.actual_state === 'running';
                  const isReady = !isRunning || nodeState?.is_ready !== false;

                  return (
                    <div
                      key={tab.nodeId}
                      className="h-full"
                      style={{ backgroundColor: 'rgb(11 15 22 / var(--tasklog-opacity, 0.92))' }}
                    >
                      <TerminalSession
                        labId={labId}
                        nodeId={tab.nodeId}
                        isActive={showConsoles}
                        isReady={isReady}
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </>
      )}

      {/* Tab drag ghost */}
      {tabDragState?.isDragging && (
        <div
          className="fixed z-[200] bg-stone-800 border border-stone-600 rounded px-3 py-1.5 text-[10px] font-bold text-sage-400 shadow-xl pointer-events-none"
          style={{
            left: tabDragState.startX + 10,
            top: tabDragState.currentY + 10,
          }}
        >
          <i className="fa-solid fa-terminal mr-2"></i>
          {consoleTabs.find(t => t.nodeId === tabDragState.nodeId)?.nodeName}
        </div>
      )}
    </div>
  );
};

export default TaskLogPanel;
