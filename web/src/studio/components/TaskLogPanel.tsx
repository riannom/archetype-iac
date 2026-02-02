import React, { useState, useCallback, useEffect, useRef } from 'react';
import TerminalSession from './TerminalSession';

export interface TaskLogEntry {
  id: string;
  timestamp: Date;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  jobId?: string;
}

interface NodeStateEntry {
  id: string;
  node_id: string;
  actual_state: string;
  is_ready?: boolean;
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
  onEntryClick?: (entry: TaskLogEntry) => void;
  // Console tabs support
  consoleTabs?: DockedConsole[];
  activeTabId?: string; // 'log' | nodeId
  onSelectTab?: (tabId: string) => void;
  onCloseConsoleTab?: (nodeId: string) => void;
  onUndockConsole?: (nodeId: string, x: number, y: number) => void;
  // Lab context for terminals
  labId?: string;
  nodeStates?: Record<string, NodeStateEntry>;
}

const MIN_HEIGHT = 100;
const MAX_HEIGHT = 600;
const DEFAULT_HEIGHT = 200;
const STORAGE_KEY = 'archetype-tasklog-height';
const TAB_UNDOCK_THRESHOLD = 30; // pixels of vertical drag to trigger undock

const TaskLogPanel: React.FC<TaskLogPanelProps> = ({
  entries,
  isVisible,
  onToggle,
  onClear,
  onEntryClick,
  consoleTabs = [],
  activeTabId = 'log',
  onSelectTab,
  onCloseConsoleTab,
  onUndockConsole,
  labId,
  nodeStates = {},
}) => {
  const errorCount = entries.filter((e) => e.level === 'error').length;
  const hasConsoleTabs = consoleTabs.length > 0;

  const [height, setHeight] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? Math.min(Math.max(parseInt(saved, 10), MIN_HEIGHT), MAX_HEIGHT) : DEFAULT_HEIGHT;
  });
  const [isResizing, setIsResizing] = useState(false);
  const startY = useRef(0);
  const startHeight = useRef(0);

  // Tab drag state for undocking
  const [tabDragState, setTabDragState] = useState<{
    nodeId: string;
    startX: number;
    startY: number;
    currentY: number;
    isDragging: boolean;
  } | null>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
    startY.current = e.clientY;
    startHeight.current = height;
  }, [height]);

  // Handle tab mousedown for potential undock drag
  const handleTabMouseDown = useCallback((e: React.MouseEvent, nodeId: string) => {
    if (!onUndockConsole) return;
    e.stopPropagation();
    setTabDragState({
      nodeId,
      startX: e.clientX,
      startY: e.clientY,
      currentY: e.clientY,
      isDragging: false,
    });
  }, [onUndockConsole]);

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

  // Handle tab drag for undocking
  useEffect(() => {
    if (!tabDragState) return;

    const handleMouseMove = (e: MouseEvent) => {
      const deltaY = e.clientY - tabDragState.startY;

      setTabDragState(prev => {
        if (!prev) return null;
        // If vertical movement exceeds threshold, mark as dragging
        if (Math.abs(deltaY) >= TAB_UNDOCK_THRESHOLD) {
          return { ...prev, currentY: e.clientY, isDragging: true };
        }
        return { ...prev, currentY: e.clientY };
      });
    };

    const handleMouseUp = (e: MouseEvent) => {
      if (tabDragState.isDragging && onUndockConsole) {
        // Undock the console at cursor position
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
  }, [tabDragState, onUndockConsole]);

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

  return (
    <div className="shrink-0 bg-white/95 dark:bg-stone-950/95 backdrop-blur-md border-t border-stone-200 dark:border-stone-800">
      {/* Header with integrated resize handle at top */}
      <div
        className={`flex justify-between items-center px-4 py-2 select-none ${
          isVisible ? 'cursor-ns-resize' : 'cursor-pointer'
        } hover:bg-stone-100/50 dark:hover:bg-stone-900/50 group relative`}
        onMouseDown={isVisible ? handleMouseDown : undefined}
        onClick={isVisible ? undefined : onToggle}
      >
        {/* Resize grip indicator - only when expanded */}
        {isVisible && (
          <div className="absolute top-0 left-0 right-0 h-1 flex items-center justify-center">
            <div className={`w-10 h-1 rounded-full transition-colors ${
              isResizing ? 'bg-cyan-500' : 'bg-stone-300 dark:bg-stone-600 group-hover:bg-cyan-400'
            }`} />
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-stone-600 dark:text-stone-400">
            {hasConsoleTabs ? 'Panel' : 'Task Log'}
          </span>
          {errorCount > 0 && isLogTabActive && (
            <span className="px-1.5 py-0.5 bg-red-600 text-white text-[9px] font-bold rounded-full">
              {errorCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {isVisible && isLogTabActive && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onClear();
              }}
              className="text-[10px] font-bold text-stone-500 hover:text-stone-700 dark:hover:text-stone-300 uppercase tracking-widest"
            >
              Clear
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }}
            className="text-stone-400 dark:text-stone-500 text-xs hover:text-stone-600 dark:hover:text-stone-300 px-1"
          >
            {isVisible ? 'v' : '^'}
          </button>
        </div>
      </div>

      {isVisible && (
        <>
          {/* Tab bar - only show when there are console tabs */}
          {hasConsoleTabs && (
            <div className="flex items-center border-b border-stone-200 dark:border-stone-800 bg-stone-50 dark:bg-stone-900/50 px-2">
              {/* Log tab */}
              <button
                onClick={() => onSelectTab?.('log')}
                className={`h-8 px-4 flex items-center gap-2 text-[10px] font-bold border-b-2 transition-all ${
                  isLogTabActive
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
              {consoleTabs.map((tab) => {
                const isActive = activeTabId === tab.nodeId;
                const isBeingDragged = tabDragState?.nodeId === tab.nodeId && tabDragState.isDragging;

                return (
                  <div
                    key={tab.nodeId}
                    onMouseDown={(e) => handleTabMouseDown(e, tab.nodeId)}
                    onClick={() => onSelectTab?.(tab.nodeId)}
                    className={`h-8 px-4 flex items-center gap-2 text-[10px] font-bold border-b-2 transition-all cursor-pointer shrink-0 ${
                      isActive
                        ? 'text-sage-600 dark:text-sage-400 border-sage-500'
                        : 'text-stone-500 dark:text-stone-500 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
                    } ${isBeingDragged ? 'opacity-50' : ''} ${onUndockConsole ? 'cursor-grab active:cursor-grabbing' : ''}`}
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
                );
              })}
            </div>
          )}

          {/* Content area */}
          <div
            className="overflow-hidden"
            style={{ height: `${height}px` }}
          >
            {/* Log content */}
            {isLogTabActive && (
              <div className="h-full overflow-y-auto font-mono text-[11px]">
                {entries.length === 0 ? (
                  <div className="px-4 py-6 text-center text-stone-400 dark:text-stone-600">No task activity yet</div>
                ) : (
                  entries.map((entry) => {
                    const isClickable = entry.jobId && onEntryClick;
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
            {!isLogTabActive && labId && consoleTabs.map((tab) => {
              if (activeTabId !== tab.nodeId) return null;
              const nodeState = nodeStates[tab.nodeId];
              const isRunning = nodeState?.actual_state === 'running';
              const isReady = !isRunning || nodeState?.is_ready !== false;

              return (
                <div key={tab.nodeId} className="h-full bg-[#0b0f16]">
                  <TerminalSession
                    labId={labId}
                    nodeId={tab.nodeId}
                    isActive={true}
                    isReady={isReady}
                  />
                </div>
              );
            })}
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
