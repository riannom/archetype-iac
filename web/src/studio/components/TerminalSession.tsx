import React, { useEffect, useRef } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import { API_BASE_URL } from '../../api';

interface TerminalSessionProps {
  labId: string;
  nodeId: string;
  isActive?: boolean;
}

const TerminalSession: React.FC<TerminalSessionProps> = ({ labId, nodeId, isActive }) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

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

  return <div ref={containerRef} className="w-full h-full" />;
};

export default TerminalSession;
