import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import { API_BASE_URL } from '../../api';

interface TerminalSessionProps {
  labId: string;
  nodeId: string;
  isActive?: boolean;
  isReady?: boolean;  // From NodeState.is_ready
}

const MAX_RECONNECT_ATTEMPTS = 10;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 15000;

const TerminalSession: React.FC<TerminalSessionProps> = ({ labId, nodeId, isActive, isReady = true }) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const [showBootWarning, setShowBootWarning] = useState(!isReady);
  const [dismissed, setDismissed] = useState(false);

  // Reconnection state
  const [connectionState, setConnectionState] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const reconnectAttemptsRef = useRef(0);
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectRef = useRef<(() => void) | null>(null);
  const cleanupRef = useRef(false);

  // Update boot warning when isReady prop changes
  useEffect(() => {
    if (isReady) {
      setShowBootWarning(false);
    } else if (!dismissed) {
      setShowBootWarning(true);
    }
  }, [isReady, dismissed]);

  // Effect 1: Terminal lifecycle — create xterm Terminal + FitAddon + ResizeObserver
  useEffect(() => {
    if (!containerRef.current) return;

    const terminal = new Terminal({
      fontSize: 12,
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
      theme: {
        background: '#0b0f16',
        foreground: '#dbe7ff',
        cursor: '#8aa1ff',
      },
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(containerRef.current);
    fitAddon.fit();

    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, [labId, nodeId]);

  // Effect 2: WebSocket lifecycle — connect, reconnect, wire data
  useEffect(() => {
    cleanupRef.current = false;
    reconnectAttemptsRef.current = 0;
    setReconnectAttempts(0);
    setConnectionState('connecting');

    const handleMessage = (data: unknown) => {
      if (!terminalRef.current) return;
      if (typeof data === 'string') {
        terminalRef.current.write(data);
        return;
      }
      if (data instanceof ArrayBuffer) {
        const bytes = new Uint8Array(data);
        terminalRef.current.write(bytes);
        return;
      }
      if (data instanceof Blob) {
        data.arrayBuffer().then((buffer) => {
          const bytes = new Uint8Array(buffer);
          terminalRef.current?.write(bytes);
        });
      }
    };

    const buildWsUrl = () => {
      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      let wsUrl = `${wsProtocol}//${window.location.host}${API_BASE_URL}`;
      if (API_BASE_URL.startsWith('http')) {
        const apiUrl = new URL(API_BASE_URL);
        wsUrl = `${apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'}//${apiUrl.host}`;
      }
      const consoleUrl = `${wsUrl.replace(/\/$/, '')}/labs/${labId}/nodes/${encodeURIComponent(nodeId)}/console`;
      const token = localStorage.getItem('token');
      return token ? `${consoleUrl}?token=${encodeURIComponent(token)}` : consoleUrl;
    };

    let dataDisposable: { dispose: () => void } | null = null;

    const connectWebSocket = () => {
      if (cleanupRef.current) return;

      const wsUrl = buildWsUrl();
      const socket = new WebSocket(wsUrl);
      socket.binaryType = 'arraybuffer';
      socketRef.current = socket;

      socket.onmessage = (event) => handleMessage(event.data);

      socket.onopen = () => {
        reconnectAttemptsRef.current = 0;
        setReconnectAttempts(0);
        setConnectionState('connected');
        terminalRef.current?.focus();
      };

      socket.onclose = () => {
        if (cleanupRef.current) return;

        setConnectionState('disconnected');

        if (reconnectAttemptsRef.current === 0) {
          terminalRef.current?.write('\r\n\x1b[33m[connection lost - reconnecting...]\x1b[0m\r\n');
        }

        if (reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS) {
          const delay = Math.min(BASE_DELAY_MS * Math.pow(2, reconnectAttemptsRef.current), MAX_DELAY_MS);
          reconnectAttemptsRef.current += 1;
          setReconnectAttempts(reconnectAttemptsRef.current);
          reconnectTimeoutRef.current = setTimeout(connectWebSocket, delay);
        } else {
          terminalRef.current?.write('\r\n\x1b[31m[reconnection failed - click Reconnect to try again]\x1b[0m\r\n');
        }
      };

      // Wire terminal input to current socket via ref
      if (dataDisposable) {
        dataDisposable.dispose();
      }
      if (terminalRef.current) {
        dataDisposable = terminalRef.current.onData((data) => {
          if (socketRef.current?.readyState === WebSocket.OPEN) {
            socketRef.current.send(data);
          }
        });
      }
    };

    connectRef.current = connectWebSocket;
    connectWebSocket();

    return () => {
      cleanupRef.current = true;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (dataDisposable) {
        dataDisposable.dispose();
      }
      if (socketRef.current) {
        socketRef.current.onclose = null; // prevent reconnect on intentional close
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, [labId, nodeId]);

  useEffect(() => {
    if (isActive) {
      fitAddonRef.current?.fit();
      terminalRef.current?.focus();
    }
  }, [isActive]);

  const handleManualReconnect = useCallback(() => {
    reconnectAttemptsRef.current = 0;
    setReconnectAttempts(0);
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (socketRef.current) {
      socketRef.current.onclose = null;
      socketRef.current.close();
    }
    setConnectionState('connecting');
    connectRef.current?.();
  }, []);

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="w-full h-full" />
      {showBootWarning && !dismissed && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/70 z-10">
          <div className="bg-slate-800 border border-slate-600 rounded-lg p-6 max-w-md text-center">
            <div className="flex items-center justify-center mb-4">
              <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-500 border-t-transparent"></div>
            </div>
            <h3 className="text-lg font-semibold text-white mb-2">Device Booting</h3>
            <p className="text-slate-300 text-sm mb-4">
              The network device is still starting up. Console may be unresponsive until boot completes.
            </p>
            <button
              onClick={() => setDismissed(true)}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded transition-colors"
            >
              Connect Anyway
            </button>
          </div>
        </div>
      )}
      {connectionState === 'disconnected' && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60 z-10">
          <div className="bg-stone-800 border border-stone-600 rounded-lg p-5 max-w-sm text-center">
            {reconnectAttempts < MAX_RECONNECT_ATTEMPTS ? (
              <>
                <div className="flex items-center justify-center mb-3">
                  <div className="animate-pulse rounded-full h-6 w-6 border-2 border-amber-500 border-t-transparent animate-spin"></div>
                </div>
                <h3 className="text-sm font-semibold text-amber-400 mb-1">Connection Lost</h3>
                <p className="text-stone-400 text-xs mb-3">
                  Reconnecting... (attempt {reconnectAttempts}/{MAX_RECONNECT_ATTEMPTS})
                </p>
                <button
                  onClick={handleManualReconnect}
                  className="px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white text-xs rounded transition-colors"
                >
                  Reconnect Now
                </button>
              </>
            ) : (
              <>
                <div className="flex items-center justify-center mb-3">
                  <div className="rounded-full h-6 w-6 border-2 border-red-500 flex items-center justify-center">
                    <i className="fa-solid fa-xmark text-red-500 text-xs"></i>
                  </div>
                </div>
                <h3 className="text-sm font-semibold text-red-400 mb-1">Reconnection Failed</h3>
                <p className="text-stone-400 text-xs mb-3">
                  Unable to reconnect after {MAX_RECONNECT_ATTEMPTS} attempts.
                </p>
                <button
                  onClick={handleManualReconnect}
                  className="px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white text-xs rounded transition-colors"
                >
                  Try Again
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default TerminalSession;
