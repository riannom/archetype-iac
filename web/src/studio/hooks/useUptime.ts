import { useState, useEffect } from 'react';
import { formatUptime } from '../../utils/format';
import { NodeStateEntry } from '../../types/nodeState';

export function useUptime(nodeStates: Record<string, NodeStateEntry>): string {
  const [uptime, setUptime] = useState<string>('--:--:--');

  useEffect(() => {
    const calculateUptime = () => {
      const runningNodes = Object.values(nodeStates).filter(
        (state) => state.actual_state === 'running' && state.boot_started_at
      );

      if (runningNodes.length === 0) {
        setUptime('--:--:--');
        return;
      }

      const earliestBoot = runningNodes.reduce((earliest, node) => {
        const bootTime = new Date(node.boot_started_at!).getTime();
        return bootTime < earliest ? bootTime : earliest;
      }, Infinity);

      const elapsed = Date.now() - earliestBoot;
      setUptime(formatUptime(elapsed));
    };

    calculateUptime();
    const interval = setInterval(calculateUptime, 1000);
    return () => clearInterval(interval);
  }, [nodeStates]);

  return uptime;
}
