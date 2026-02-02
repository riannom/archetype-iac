/**
 * WebSocket hook for real-time lab state updates.
 *
 * This hook connects to the WebSocket endpoint and receives push notifications
 * for state changes, eliminating the need for polling. It includes:
 * - Automatic reconnection with exponential backoff
 * - Initial state snapshot on connect
 * - Incremental updates for nodes, links, and jobs
 *
 * Usage:
 * ```tsx
 * const { nodeStates, linkStates, isConnected } = useLabStateWS(labId, {
 *   onNodeStateChange: (nodeId, state) => { ... },
 *   onLinkStateChange: (linkName, state) => { ... },
 * });
 * ```
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import { API_BASE_URL } from '../../api';

export interface NodeStateData {
  node_id: string;
  node_name: string;
  desired_state: 'running' | 'stopped';
  actual_state: 'undeployed' | 'pending' | 'starting' | 'running' | 'stopped' | 'stopping' | 'error';
  is_ready: boolean;
  error_message?: string | null;
  host_id?: string | null;
  host_name?: string | null;
}

export interface LinkStateData {
  link_name: string;
  desired_state: 'up' | 'down';
  actual_state: 'up' | 'down' | 'pending' | 'error' | 'unknown';
  source_node: string;
  target_node: string;
  error_message?: string | null;
}

export interface LabStateData {
  lab_id: string;
  state: string;
  error?: string | null;
}

export interface JobProgressData {
  job_id: string;
  action: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress_message?: string | null;
  error_message?: string | null;
}

interface WSMessage {
  type: 'node_state' | 'link_state' | 'lab_state' | 'job_progress' | 'initial_state' | 'initial_links' | 'heartbeat' | 'pong' | 'error';
  timestamp: string;
  data: unknown;
}

export interface UseLabStateWSOptions {
  /** Callback when a node state changes */
  onNodeStateChange?: (nodeId: string, state: NodeStateData) => void;
  /** Callback when a link state changes */
  onLinkStateChange?: (linkName: string, state: LinkStateData) => void;
  /** Callback when lab state changes */
  onLabStateChange?: (state: LabStateData) => void;
  /** Callback when job progress updates */
  onJobProgress?: (job: JobProgressData) => void;
  /** Whether to enable the WebSocket connection (default: true) */
  enabled?: boolean;
  /** Fallback polling interval in ms when WebSocket fails (default: 4000) */
  fallbackPollingInterval?: number;
}

export interface UseLabStateWSResult {
  /** Current node states by node_id */
  nodeStates: Map<string, NodeStateData>;
  /** Current link states by link_name */
  linkStates: Map<string, LinkStateData>;
  /** Current lab state */
  labState: LabStateData | null;
  /** Whether WebSocket is currently connected */
  isConnected: boolean;
  /** Number of reconnection attempts */
  reconnectAttempts: number;
  /** Force a refresh of state from server */
  refresh: () => void;
}

/**
 * Hook for real-time lab state updates via WebSocket.
 *
 * @param labId - Lab ID to subscribe to
 * @param options - Configuration options
 * @returns State and connection info
 */
export function useLabStateWS(
  labId: string | null,
  options: UseLabStateWSOptions = {}
): UseLabStateWSResult {
  const {
    onNodeStateChange,
    onLinkStateChange,
    onLabStateChange,
    onJobProgress,
    enabled = true,
  } = options;

  const [nodeStates, setNodeStates] = useState<Map<string, NodeStateData>>(new Map());
  const [linkStates, setLinkStates] = useState<Map<string, LinkStateData>>(new Map());
  const [labState, setLabState] = useState<LabStateData | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [reconnectAttempts, setReconnectAttempts] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const pingIntervalRef = useRef<number | null>(null);

  // Build WebSocket URL from API URL
  const getWSUrl = useCallback(() => {
    // Convert http(s):// to ws(s)://
    let baseUrl = API_BASE_URL;

    // Handle relative URLs (e.g., "/api")
    if (baseUrl.startsWith('/')) {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      baseUrl = `${protocol}//${window.location.host}${baseUrl}`;
    } else {
      baseUrl = baseUrl.replace(/^http/, 'ws');
    }

    return `${baseUrl}/ws/labs/${labId}/state`;
  }, [labId]);

  // Handle incoming messages
  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const message: WSMessage = JSON.parse(event.data);

      switch (message.type) {
        case 'initial_state': {
          // Batch update of all node states
          const data = message.data as { nodes: NodeStateData[] };
          const newStates = new Map<string, NodeStateData>();
          data.nodes.forEach((node) => {
            newStates.set(node.node_id, node);
          });
          setNodeStates(newStates);
          break;
        }

        case 'initial_links': {
          // Batch update of all link states
          const data = message.data as { links: LinkStateData[] };
          const newLinkStates = new Map<string, LinkStateData>();
          data.links.forEach((link) => {
            newLinkStates.set(link.link_name, link);
          });
          setLinkStates(newLinkStates);
          break;
        }

        case 'node_state': {
          const data = message.data as NodeStateData;
          setNodeStates((prev) => {
            const newMap = new Map(prev);
            newMap.set(data.node_id, data);
            return newMap;
          });
          onNodeStateChange?.(data.node_id, data);
          break;
        }

        case 'link_state': {
          const data = message.data as LinkStateData;
          setLinkStates((prev) => {
            const newMap = new Map(prev);
            newMap.set(data.link_name, data);
            return newMap;
          });
          onLinkStateChange?.(data.link_name, data);
          break;
        }

        case 'lab_state': {
          const data = message.data as LabStateData;
          setLabState(data);
          onLabStateChange?.(data);
          break;
        }

        case 'job_progress': {
          const data = message.data as JobProgressData;
          onJobProgress?.(data);
          break;
        }

        case 'heartbeat':
        case 'pong':
          // Connection is alive, nothing to do
          break;

        case 'error': {
          const data = message.data as { message: string };
          console.error('WebSocket error from server:', data.message);
          break;
        }
      }
    } catch (e) {
      console.error('Failed to parse WebSocket message:', e);
    }
  }, [onNodeStateChange, onLinkStateChange, onLabStateChange, onJobProgress]);

  // Connect to WebSocket
  const connect = useCallback(() => {
    if (!labId || !enabled) return;

    const url = getWSUrl();
    console.log('Connecting to WebSocket:', url);

    try {
      const ws = new WebSocket(url);

      ws.onopen = () => {
        console.log('WebSocket connected');
        setIsConnected(true);
        setReconnectAttempts(0);

        // Start ping interval to keep connection alive
        pingIntervalRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 25000);
      };

      ws.onmessage = handleMessage;

      ws.onclose = (event) => {
        console.log('WebSocket closed:', event.code, event.reason);
        setIsConnected(false);

        // Clear ping interval
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = null;
        }

        // Reconnect with exponential backoff
        if (enabled && labId) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
          console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttempts + 1})`);
          reconnectTimeoutRef.current = window.setTimeout(() => {
            setReconnectAttempts((prev) => prev + 1);
            connect();
          }, delay);
        }
      };

      ws.onerror = (error) => {
        console.error('WebSocket error:', error);
      };

      wsRef.current = ws;
    } catch (e) {
      console.error('Failed to create WebSocket:', e);
    }
  }, [labId, enabled, getWSUrl, handleMessage, reconnectAttempts]);

  // Request state refresh
  const refresh = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'refresh' }));
    }
  }, []);

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    connect();

    return () => {
      // Clean up
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmounting');
        wsRef.current = null;
      }
    };
  }, [connect]);

  // Reset state when lab changes
  useEffect(() => {
    setNodeStates(new Map());
    setLinkStates(new Map());
    setLabState(null);
    setReconnectAttempts(0);
  }, [labId]);

  return {
    nodeStates,
    linkStates,
    labState,
    isConnected,
    reconnectAttempts,
    refresh,
  };
}
