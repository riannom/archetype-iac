import React from 'react';

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

const TaskLogPanel: React.FC<TaskLogPanelProps> = ({ entries, isVisible, onToggle, onClear, onEntryClick }) => {
  const errorCount = entries.filter((e) => e.level === 'error').length;

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
    <div className="shrink-0 bg-white/95 dark:bg-stone-950/95 border-t border-stone-200 dark:border-stone-800 backdrop-blur-md">
      <div
        onClick={onToggle}
        className="flex justify-between items-center px-4 py-2 cursor-pointer hover:bg-stone-100/50 dark:hover:bg-stone-900/50 select-none"
      >
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
          <span className="text-stone-400 dark:text-stone-500 text-xs">{isVisible ? 'v' : '^'}</span>
        </div>
      </div>
      {isVisible && (
        <div className="max-h-[200px] overflow-y-auto font-mono text-[11px]">
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
