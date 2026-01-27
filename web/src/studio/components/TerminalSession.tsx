import React, { useEffect, useRef, useState } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import { API_BASE_URL } from '../../api';

interface TerminalSessionProps {
  labId: string;
  nodeId: string;
  isActive?: boolean;
  isReady?: boolean;  // From NodeState.is_ready
}

const TerminalSession: React.FC<TerminalSessionProps> = ({ labId, nodeId, isActive, isReady = true }) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const [showBootWarning, setShowBootWarning] = useState(!isReady);
  const [dismissed, setDismissed] = useState(false);

  // Update boot warning when isReady prop changes
  useEffect(() => {
    if (isReady) {
      setShowBootWarning(false);
    } else if (!dismissed) {
      setShowBootWarning(true);
    }
  }, [isReady, dismissed]);

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

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = `${wsProtocol}//${window.location.host}${API_BASE_URL}`;
    if (API_BASE_URL.startsWith('http')) {
      const apiUrl = new URL(API_BASE_URL);
      wsUrl = `${apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'}//${apiUrl.host}`;
    }
    wsUrl = `${wsUrl.replace(/\/$/, '')}/labs/${labId}/nodes/${encodeURIComponent(nodeId)}/console`;

    const socket = new WebSocket(wsUrl);
    socket.binaryType = 'arraybuffer';
    socket.onmessage = (event) => handleMessage(event.data);
    socket.onclose = () => {
      terminalRef.current?.writeln('\n[console disconnected]\n');
    };
    socket.onopen = () => {
      terminalRef.current?.focus();
    };
    socketRef.current = socket;

    const dataDisposable = terminal.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(data);
      }
    });

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      dataDisposable.dispose();
      resizeObserver.disconnect();
      socket.close();
      terminal.dispose();
      socketRef.current = null;
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, [labId, nodeId]);

  useEffect(() => {
    if (isActive) {
      fitAddonRef.current?.fit();
      terminalRef.current?.focus();
    }
  }, [isActive]);

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
    </div>
  );
};

export default TerminalSession;
