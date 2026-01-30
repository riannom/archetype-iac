import React, { useState, useCallback, useEffect, useRef } from 'react';

export interface TaskLogEntry {
  id: string;
  timestamp: Date;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  jobId?: string;
}

interface TaskLogPanelProps {
  entries: TaskLogEntry[];
  isVisible: boolean;
  onToggle: () => void;
  onClear: () => void;
  onEntryClick?: (entry: TaskLogEntry) => void;
}

const MIN_HEIGHT = 100;
const MAX_HEIGHT = 600;
const DEFAULT_HEIGHT = 200;
const STORAGE_KEY = 'archetype-tasklog-height';

const TaskLogPanel: React.FC<TaskLogPanelProps> = ({ entries, isVisible, onToggle, onClear, onEntryClick }) => {
  const errorCount = entries.filter((e) => e.level === 'error').length;

  const [height, setHeight] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? Math.min(Math.max(parseInt(saved, 10), MIN_HEIGHT), MAX_HEIGHT) : DEFAULT_HEIGHT;
  });
  const [isResizing, setIsResizing] = useState(false);
  const startY = useRef(0);
  const startHeight = useRef(0);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
    startY.current = e.clientY;
    startHeight.current = height;
  }, [height]);

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
            Task Log
          </span>
          {errorCount > 0 && (
            <span className="px-1.5 py-0.5 bg-red-600 text-white text-[9px] font-bold rounded-full">
              {errorCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {isVisible && (
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
        <div
          className="overflow-y-auto font-mono text-[11px]"
          style={{ height: `${height}px` }}
        >
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
    </div>
  );
};

export default TaskLogPanel;
