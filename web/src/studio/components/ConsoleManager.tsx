import React, { useState, useEffect } from 'react';
import { ConsoleWindow, Node } from '../types';
import TerminalSession from './TerminalSession';

interface NodeStateEntry {
  id: string;
  node_id: string;
  actual_state: string;
  is_ready?: boolean;
}

interface ConsoleManagerProps {
  labId: string;
  windows: ConsoleWindow[];
  nodes: Node[];
  nodeStates?: Record<string, NodeStateEntry>;
  onCloseWindow: (windowId: string) => void;
  onCloseTab: (windowId: string, nodeId: string) => void;
  onSetActiveTab: (windowId: string, nodeId: string) => void;
  onUpdateWindowPos: (windowId: string, x: number, y: number) => void;
  onUpdateWindowSize?: (windowId: string, width: number, height: number) => void;
}

const ConsoleManager: React.FC<ConsoleManagerProps> = ({
  labId,
  windows,
  nodes,
  nodeStates = {},
  onCloseWindow,
  onCloseTab,
  onSetActiveTab,
  onUpdateWindowPos,
}) => {
  const [dragState, setDragState] = useState<{ id: string; startX: number; startY: number } | null>(null);
  const [resizeState, setResizeState] = useState<{ id: string; startWidth: number; startHeight: number; startX: number; startY: number } | null>(null);
  const [winSizes, setWinSizes] = useState<Record<string, { w: number; h: number }>>({});

  const handleMouseDown = (e: React.MouseEvent, win: ConsoleWindow) => {
    setDragState({ id: win.id, startX: e.clientX - win.x, startY: e.clientY - win.y });
  };

  const handleResizeMouseDown = (e: React.MouseEvent, win: ConsoleWindow) => {
    e.stopPropagation();
    e.preventDefault();
    const currentW = winSizes[win.id]?.w || 520;
    const currentH = winSizes[win.id]?.h || 360;
    setResizeState({
      id: win.id,
      startWidth: currentW,
      startHeight: currentH,
      startX: e.clientX,
      startY: e.clientY,
    });
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (dragState) {
        onUpdateWindowPos(dragState.id, e.clientX - dragState.startX, e.clientY - dragState.startY);
      }
      if (resizeState) {
        const deltaX = e.clientX - resizeState.startX;
        const deltaY = e.clientY - resizeState.startY;
        setWinSizes((prev) => ({
          ...prev,
          [resizeState.id]: {
            w: Math.max(320, resizeState.startWidth + deltaX),
            h: Math.max(240, resizeState.startHeight + deltaY),
          },
        }));
      }
    };

    const handleMouseUp = () => {
      setDragState(null);
      setResizeState(null);
    };

    if (dragState || resizeState) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    }
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [dragState, resizeState, onUpdateWindowPos]);

  return (
    <>
      {windows.map((win) => {
        const size = winSizes[win.id] || { w: 520, h: 360 };
        const activeNode = nodes.find((n) => n.id === win.activeDeviceId);

        return (
          <div
            key={win.id}
            className="fixed z-40 bg-stone-900 border border-stone-700 rounded-lg shadow-2xl flex flex-col overflow-hidden ring-1 ring-white/5"
            style={{
              left: win.x,
              top: win.y,
              width: size.w,
              height: size.h,
              boxShadow:
                dragState?.id === win.id || resizeState?.id === win.id
                  ? '0 25px 50px -12px rgba(0, 0, 0, 0.7)'
                  : '0 20px 25px -5px rgba(0, 0, 0, 0.4)',
            }}
          >
            <div
              className="h-9 bg-stone-800 border-b border-stone-700 flex items-center cursor-move select-none shrink-0"
              onMouseDown={(e) => handleMouseDown(e, win)}
            >
              <div className="flex-1 flex items-center h-full overflow-x-auto no-scrollbar scroll-smooth">
                {win.deviceIds.map((nodeId) => {
                  const node = nodes.find((n) => n.id === nodeId);
                  const isActive = win.activeDeviceId === nodeId;
                  return (
                    <div
                      key={nodeId}
                      onClick={(e) => {
                        e.stopPropagation();
                        onSetActiveTab(win.id, nodeId);
                      }}
                      className={`h-full px-4 flex items-center gap-2 text-[10px] font-bold border-r border-stone-700/50 transition-all cursor-pointer shrink-0 relative
                        ${isActive ? 'bg-stone-900 text-sage-400' : 'text-stone-500 hover:bg-stone-700/50 hover:text-stone-300'}`}
                    >
                      {isActive && <div className="absolute top-0 left-0 right-0 h-0.5 bg-sage-500" />}
                      <i className={`fa-solid ${isActive ? 'fa-terminal' : 'fa-rectangle-list'} scale-90`}></i>
                      <span className="truncate max-w-[80px]">{node?.name || 'Unknown'}</span>
                      <button
                        onMouseDown={(e) => e.stopPropagation()}
                        onClick={(e) => {
                          e.stopPropagation();
                          onCloseTab(win.id, nodeId);
                        }}
                        className="ml-1 hover:text-red-400 p-0.5 transition-colors opacity-60 hover:opacity-100"
                      >
                        <i className="fa-solid fa-xmark"></i>
                      </button>
                    </div>
                  );
                })}
              </div>
              <div className="flex items-center px-2 gap-1.5 shrink-0 bg-stone-800 ml-auto border-l border-stone-700">
                <button
                  className="w-6 h-6 flex items-center justify-center text-stone-500 hover:text-stone-300 hover:bg-stone-700 rounded transition-all"
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={() => {
                    if (!activeNode) return;
                    const url = `/studio/console/${encodeURIComponent(labId)}/${encodeURIComponent(activeNode.id)}`;
                    window.open(url, `archetype-console-${activeNode.id}`, 'width=960,height=640');
                  }}
                >
                  <i className="fa-solid fa-up-right-from-square text-[9px]"></i>
                </button>
                <button
                  onClick={() => onCloseWindow(win.id)}
                  className="w-6 h-6 flex items-center justify-center text-stone-500 hover:text-red-400 hover:bg-red-400/10 rounded transition-all"
                >
                  <i className="fa-solid fa-xmark"></i>
                </button>
              </div>
            </div>

            <div className="flex-1 bg-[#0b0f16] relative">
              {win.deviceIds.length === 0 && (
                <div className="absolute inset-0 flex flex-col items-center justify-center text-stone-700">
                  <i className="fa-solid fa-terminal text-4xl mb-4 opacity-10"></i>
                  <p className="text-xs font-bold uppercase tracking-widest opacity-30">No active session selected</p>
                </div>
              )}
              {win.deviceIds.map((nodeId) => {
                const nodeState = nodeStates[nodeId];
                // Only show boot warning for running nodes that aren't ready yet
                // For error/stopped/pending states, don't show boot warning
                const isRunning = nodeState?.actual_state === 'running';
                const isReady = !isRunning || nodeState?.is_ready !== false;
                return (
                  <div
                    key={nodeId}
                    className={`absolute inset-0 ${win.activeDeviceId === nodeId ? 'block' : 'hidden'}`}
                  >
                    <TerminalSession
                      labId={labId}
                      nodeId={nodeId}
                      isActive={win.activeDeviceId === nodeId}
                      isReady={isReady}
                    />
                  </div>
                );
              })}
            </div>

            <div
              className="absolute bottom-0 right-0 w-5 h-5 cursor-nwse-resize flex items-end justify-end p-0.5 group pointer-events-auto"
              onMouseDown={(e) => handleResizeMouseDown(e, win)}
            >
              <div className="w-2 h-2 border-r-2 border-b-2 border-stone-700 group-hover:border-sage-500 transition-colors"></div>
            </div>
          </div>
        );
      })}
    </>
  );
};

export default ConsoleManager;
