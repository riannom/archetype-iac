import React, { useState, useEffect } from 'react';
import { APP_VERSION, APP_VERSION_LABEL } from '../../config';
import { formatUptime } from '../../utils/format';
import { NodeStateEntry } from '../../types/nodeState';

interface StatusBarProps {
  nodeStates: Record<string, NodeStateEntry>;
  wsConnected?: boolean;
  reconnectAttempts?: number;
}

const StatusBar: React.FC<StatusBarProps> = ({ nodeStates, wsConnected, reconnectAttempts = 0 }) => {
  const [uptime, setUptime] = useState<string>('--:--:--');

  useEffect(() => {
    const calculateUptime = () => {
      // Find all running nodes with boot_started_at set
      const runningNodes = Object.values(nodeStates).filter(
        (state) => state.actual_state === 'running' && state.boot_started_at
      );

      if (runningNodes.length === 0) {
        setUptime('--:--:--');
        return;
      }

      // Find the earliest boot_started_at timestamp
      const earliestBoot = runningNodes.reduce((earliest, node) => {
        const bootTime = new Date(node.boot_started_at!).getTime();
        return bootTime < earliest ? bootTime : earliest;
      }, Infinity);

      const elapsed = Date.now() - earliestBoot;
      setUptime(formatUptime(elapsed));
    };

    // Calculate immediately
    calculateUptime();

    // Update every second
    const interval = setInterval(calculateUptime, 1000);
    return () => clearInterval(interval);
  }, [nodeStates]);

  return (
    <div className="h-8 bg-white/90 dark:bg-stone-900/90 backdrop-blur-md border-t border-stone-200 dark:border-stone-700 flex items-center justify-between px-4 z-10 shrink-0 text-[10px] font-bold tracking-tight">
      <div className="flex items-center gap-6">
        {/* WebSocket connection indicator */}
        {wsConnected !== undefined && (
          <div className="flex items-center gap-2">
            <div
              className={`flex items-center gap-1.5 px-2 py-0.5 rounded border ${
                reconnectAttempts > 0
                  ? 'bg-amber-50 dark:bg-amber-900/30 border-amber-200 dark:border-amber-800 text-amber-600 dark:text-amber-400'
                  : 'bg-sage-100 dark:bg-sage-900 border-sage-300 dark:border-sage-700 text-sage-700 dark:text-sage-300'
              } transition-colors`}
              title={
                wsConnected
                  ? 'Connected - Receiving real-time updates'
                  : reconnectAttempts > 0
                    ? `Reconnecting... (attempt ${reconnectAttempts})`
                    : 'Disconnected - Using polling fallback'
              }
            >
              <i
                className={`fa-solid ${
                  reconnectAttempts > 0
                    ? 'fa-rotate fa-spin'
                    : 'fa-signal'
                } text-[8px]`}
              ></i>
              <span className="uppercase">
                {reconnectAttempts > 0 ? 'RECONNECTING' : 'LIVE'}
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2 text-stone-500 dark:text-stone-500 hover:text-sage-600 dark:hover:text-sage-400 cursor-pointer transition-colors">
          <i className="fa-solid fa-clock-rotate-left"></i>
          <span className="uppercase">UPTIME: {uptime}</span>
        </div>

        <div className="h-3 w-px bg-stone-200 dark:bg-stone-800"></div>

        <div className="flex items-center gap-1.5 bg-stone-100 dark:bg-stone-800 px-2 py-0.5 rounded border border-stone-200 dark:border-stone-700 text-sage-600 dark:text-sage-400 uppercase">
          <i className="fa-solid fa-code-branch text-[8px]"></i>
          <span>v{APP_VERSION}-{APP_VERSION_LABEL}</span>
        </div>
      </div>
    </div>
  );
};

export default StatusBar;
