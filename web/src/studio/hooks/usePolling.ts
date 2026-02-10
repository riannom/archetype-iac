import { useEffect, useRef } from 'react';

interface UsePollingOptions {
  immediate?: boolean;
}

export function usePolling(
  callback: () => void | Promise<void>,
  intervalMs: number,
  enabled: boolean,
  options: UsePollingOptions = {}
): void {
  const savedCallback = useRef(callback);

  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    if (options.immediate) {
      void savedCallback.current();
    }

    const id = window.setInterval(() => {
      void savedCallback.current();
    }, intervalMs);

    return () => window.clearInterval(id);
  }, [enabled, intervalMs, options.immediate]);
}
