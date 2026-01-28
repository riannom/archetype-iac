
import React, { useRef, useState, useEffect, useCallback, useMemo, memo } from 'react';
import { Node, Link, DeviceType, Annotation, DeviceModel, isExternalNetworkNode, isDeviceNode } from '../types';
import { RuntimeStatus } from './RuntimeControl';
import { useTheme } from '../../theme/index';

interface CanvasProps {
  nodes: Node[];
  links: Link[];
  annotations: Annotation[];
  runtimeStates: Record<string, RuntimeStatus>;
  deviceModels: DeviceModel[];
  onNodeMove: (id: string, x: number, y: number) => void;
  onAnnotationMove: (id: string, x: number, y: number) => void;
  onConnect: (sourceId: string, targetId: string) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onOpenConsole: (nodeId: string) => void;
  onUpdateStatus: (nodeId: string, status: RuntimeStatus) => void;
  onDelete: (id: string) => void;
}

interface ContextMenu {
  x: number;
  y: number;
  id: string;
  type: 'node' | 'link';
}

const Canvas: React.FC<CanvasProps> = ({
  nodes, links, annotations, runtimeStates, deviceModels, onNodeMove, onAnnotationMove, onConnect, selectedId, onSelect, onOpenConsole, onUpdateStatus, onDelete
}) => {
  const { effectiveMode } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const [draggingNode, setDraggingNode] = useState<string | null>(null);
  const [draggingAnnotation, setDraggingAnnotation] = useState<string | null>(null);
  const [linkingNode, setLinkingNode] = useState<string | null>(null);
  const [hoveredLinkId, setHoveredLinkId] = useState<string | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null);

  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);

  // Memoized node map for O(1) lookups instead of O(n) .find() calls
  const nodeMap = useMemo(() => {
    const map = new Map<string, Node>();
    nodes.forEach(node => map.set(node.id, node));
    return map;
  }, [nodes]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId) {
        const target = e.target as HTMLElement;
        if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA') {
          onDelete(selectedId);
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedId, onDelete]);

  useEffect(() => {
    const handleClickOutside = () => setContextMenu(null);
    window.addEventListener('click', handleClickOutside);
    return () => window.removeEventListener('click', handleClickOutside);
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left - offset.x) / zoom;
    const y = (e.clientY - rect.top - offset.y) / zoom;
    setMousePos({ x, y });

    if (isPanning) {
      setOffset(prev => ({ x: prev.x + e.movementX, y: prev.y + e.movementY }));
      return;
    }

    if (draggingNode) {
      onNodeMove(draggingNode, x, y);
    } else if (draggingAnnotation) {
      onAnnotationMove(draggingAnnotation, x, y);
    }
  }, [offset, zoom, isPanning, draggingNode, draggingAnnotation, onNodeMove, onAnnotationMove]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const factor = Math.pow(1.1, -e.deltaY / 100);
      const newZoom = Math.min(Math.max(0.1, zoom * factor), 5);
      const rect = containerRef.current!.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;
      const newOffsetX = mouseX - (mouseX - offset.x) * (newZoom / zoom);
      const newOffsetY = mouseY - (mouseY - offset.y) * (newZoom / zoom);
      setZoom(newZoom);
      setOffset({ x: newOffsetX, y: newOffsetY });
    } else {
      setOffset(prev => ({ x: prev.x - e.deltaX, y: prev.y - e.deltaY }));
    }
  }, [zoom, offset]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button === 1 || (e.button === 0 && (e as any).spaceKey)) {
      setIsPanning(true);
      return;
    }
    onSelect(null);
    setContextMenu(null);
  }, [onSelect]);

  const handleMouseUp = useCallback(() => {
    setDraggingNode(null);
    setDraggingAnnotation(null);
    setIsPanning(false);
    setLinkingNode(null);
  }, []);

  const handleNodeMouseDown = (e: React.MouseEvent, id: string) => {
    if (e.button === 2) return;
    e.stopPropagation();
    setContextMenu(null);
    if (e.shiftKey) {
      setLinkingNode(id);
    } else {
      setDraggingNode(id);
      onSelect(id);
    }
  };

  const handleAnnotationMouseDown = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    setDraggingAnnotation(id);
    onSelect(id);
  };

  const handleNodeContextMenu = (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    onSelect(id);
    setContextMenu({ x: e.clientX, y: e.clientY, id, type: 'node' });
  };

  const handleLinkContextMenu = (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    onSelect(id);
    setContextMenu({ x: e.clientX, y: e.clientY, id, type: 'link' });
  };

  const handleNodeMouseUp = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (linkingNode && linkingNode !== id) {
      onConnect(linkingNode, id);
    }
    setLinkingNode(null);
    setDraggingNode(null);
  };

  const handleLinkMouseDown = (e: React.MouseEvent, id: string) => {
    if (e.button === 2) return;
    e.stopPropagation();
    setContextMenu(null);
    onSelect(id);
  };

  const centerCanvas = () => { setZoom(1); setOffset({ x: 0, y: 0 }); };

  const fitToScreen = () => {
    if (!containerRef.current || nodes.length === 0) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(n => {
      minX = Math.min(minX, n.x - 50);
      minY = Math.min(minY, n.y - 50);
      maxX = Math.max(maxX, n.x + 50);
      maxY = Math.max(maxY, n.y + 50);
    });
    const rect = containerRef.current.getBoundingClientRect();
    const contentW = maxX - minX;
    const contentH = maxY - minY;
    const newZoom = Math.min(rect.width / contentW, rect.height / contentH, 1) * 0.9;
    setZoom(newZoom);
    setOffset({
      x: (rect.width - contentW * newZoom) / 2 - minX * newZoom,
      y: (rect.height - contentH * newZoom) / 2 - minY * newZoom,
    });
  };

  const getNodeIcon = (modelId: string) => deviceModels.find(m => m.id === modelId)?.icon || 'fa-arrows-to-dot';

  const handleAction = (action: string) => {
    if (contextMenu) {
      switch (action) {
        case 'delete': onDelete(contextMenu.id); break;
        case 'console': onOpenConsole(contextMenu.id); break;
        case 'start': onUpdateStatus(contextMenu.id, 'booting'); break;
        case 'stop': onUpdateStatus(contextMenu.id, 'stopped'); break;
        case 'reload': onUpdateStatus(contextMenu.id, 'booting'); break;
      }
      setContextMenu(null);
    }
  };

  return (
    <div
      ref={containerRef}
      className={`flex-1 relative overflow-hidden canvas-grid ${isPanning ? 'cursor-grabbing' : 'cursor-crosshair'}`}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseDown={handleMouseDown}
      onWheel={handleWheel}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div
        className="absolute inset-0 origin-top-left"
        style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})` }}
      >
        <svg className="absolute inset-0 w-[5000px] h-[5000px] pointer-events-none">
          {annotations.map(ann => {
            const isSelected = selectedId === ann.id;
            const stroke = isSelected ? (effectiveMode === 'dark' ? '#65A30D' : '#4D7C0F') : (ann.color || (effectiveMode === 'dark' ? '#57534E' : '#D6D3D1'));
            return (
              <g key={ann.id} className="pointer-events-auto cursor-move" onMouseDown={(e) => handleAnnotationMouseDown(e, ann.id)}>
                {ann.type === 'rect' && <rect x={ann.x} y={ann.y} width={ann.width || 100} height={ann.height || 60} fill={effectiveMode === 'dark' ? "rgba(68, 64, 60, 0.2)" : "rgba(214, 211, 209, 0.2)"} stroke={stroke} strokeWidth="2" strokeDasharray={isSelected ? "4" : "0"} rx="4" />}
                {ann.type === 'circle' && <circle cx={ann.x} cy={ann.y} r={ann.width ? ann.width / 2 : 40} fill={effectiveMode === 'dark' ? "rgba(68, 64, 60, 0.2)" : "rgba(214, 211, 209, 0.2)"} stroke={stroke} strokeWidth="2" strokeDasharray={isSelected ? "4" : "0"} />}
                {ann.type === 'text' && <text x={ann.x} y={ann.y} fill={ann.color || (effectiveMode === 'dark' ? 'white' : '#1C1917')} fontSize={ann.fontSize || 14} className="font-black tracking-tight select-none">{ann.text || 'New Text'}</text>}
              </g>
            );
          })}

          {links.map(link => {
            const source = nodeMap.get(link.source);
            const target = nodeMap.get(link.target);
            if (!source || !target) return null;
            const isSelected = selectedId === link.id;
            const isHovered = hoveredLinkId === link.id;

            // Check if either endpoint is an external network node
            const isExternalLink = isExternalNetworkNode(source) || isExternalNetworkNode(target);

            // Use blue colors for external links, green/gray for regular links
            let linkColor: string;
            if (isExternalLink) {
              linkColor = isSelected
                ? (effectiveMode === 'dark' ? '#3B82F6' : '#2563EB')
                : (isHovered ? (effectiveMode === 'dark' ? '#60A5FA' : '#3B82F6') : (effectiveMode === 'dark' ? '#6366F1' : '#A5B4FC'));
            } else {
              linkColor = isSelected
                ? (effectiveMode === 'dark' ? '#65A30D' : '#4D7C0F')
                : (isHovered ? (effectiveMode === 'dark' ? '#84CC16' : '#65A30D') : (effectiveMode === 'dark' ? '#57534E' : '#D6D3D1'));
            }

            // Calculate port label positions (~40px along line from each node, offset perpendicular)
            const dx = target.x - source.x;
            const dy = target.y - source.y;
            const len = Math.sqrt(dx * dx + dy * dy);
            const unitX = len > 0 ? dx / len : 0;
            const unitY = len > 0 ? dy / len : 0;
            // Perpendicular offset (rotate 90 degrees)
            const perpX = -unitY;
            const perpY = unitX;
            const labelOffset = 40;
            const perpOffset = 10; // offset perpendicular to the line
            const sourceLabelX = source.x + unitX * labelOffset + perpX * perpOffset;
            const sourceLabelY = source.y + unitY * labelOffset + perpY * perpOffset;
            const targetLabelX = target.x - unitX * labelOffset + perpX * perpOffset;
            const targetLabelY = target.y - unitY * labelOffset + perpY * perpOffset;

            // Label styling for better contrast
            const labelColor = effectiveMode === 'dark' ? '#E7E5E4' : '#44403C';
            const labelStroke = effectiveMode === 'dark' ? '#1C1917' : '#FFFFFF';

            return (
              <g key={link.id} className="pointer-events-auto cursor-pointer">
                <line x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="transparent" strokeWidth="12" onMouseDown={(e) => handleLinkMouseDown(e, link.id)} onMouseEnter={() => setHoveredLinkId(link.id)} onMouseLeave={() => setHoveredLinkId(null)} />
                <line
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={linkColor}
                  strokeWidth={isSelected || isHovered ? "3" : "2"}
                  strokeDasharray={isExternalLink ? "6 4" : undefined}
                  className={draggingNode ? '' : 'transition-[stroke,stroke-width] duration-150'}
                />
                {/* Port labels */}
                {link.sourceInterface && (
                  <text
                    x={sourceLabelX}
                    y={sourceLabelY}
                    fill={labelColor}
                    stroke={labelStroke}
                    strokeWidth="3"
                    paintOrder="stroke"
                    fontSize="11"
                    fontWeight="700"
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="pointer-events-none select-none"
                    style={{ fontFamily: 'ui-monospace, monospace' }}
                  >
                    {link.sourceInterface}
                  </text>
                )}
                {link.targetInterface && (
                  <text
                    x={targetLabelX}
                    y={targetLabelY}
                    fill={labelColor}
                    stroke={labelStroke}
                    strokeWidth="3"
                    paintOrder="stroke"
                    fontSize="11"
                    fontWeight="700"
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="pointer-events-none select-none"
                    style={{ fontFamily: 'ui-monospace, monospace' }}
                  >
                    {link.targetInterface}
                  </text>
                )}
              </g>
            );
          })}

          {linkingNode && (
            <line
              x1={nodeMap.get(linkingNode)?.x}
              y1={nodeMap.get(linkingNode)?.y}
              x2={mousePos.x}
              y2={mousePos.y}
              stroke="#65A30D"
              strokeWidth="2"
              strokeDasharray="4"
            />
          )}
        </svg>

        {nodes.map(node => {
          // Check if this is an external network node
          if (isExternalNetworkNode(node)) {
            // Render external network node with cloud shape
            const extNode = node;
            const vlanLabel = extNode.connectionType === 'vlan'
              ? `VLAN ${extNode.vlanId || '?'}`
              : extNode.bridgeName || 'Bridge';

            return (
              <div
                key={node.id}
                style={{ left: node.x - 28, top: node.y - 20 }}
                onMouseDown={(e) => handleNodeMouseDown(e, node.id)}
                onMouseUp={(e) => handleNodeMouseUp(e, node.id)}
                onContextMenu={(e) => handleNodeContextMenu(e, node.id)}
                className={`absolute w-14 h-10 flex items-center justify-center cursor-pointer shadow-md transition-[box-shadow,background-color,border-color,transform] duration-150 rounded-2xl
                  ${selectedId === node.id
                    ? 'ring-2 ring-blue-500 bg-gradient-to-br from-blue-100 to-purple-100 dark:from-blue-900/60 dark:to-purple-900/60 shadow-lg shadow-blue-500/20'
                    : 'bg-gradient-to-br from-blue-50 to-purple-50 dark:from-blue-950/40 dark:to-purple-950/40 border border-blue-300 dark:border-blue-700'}
                  ${linkingNode === node.id ? 'ring-2 ring-blue-400 scale-110' : ''}
                  hover:border-blue-400 z-10 select-none group`}
              >
                <i className="fa-solid fa-cloud text-blue-500 dark:text-blue-400 text-lg"></i>
                <div className="absolute top-full mt-1 text-[10px] font-bold text-blue-700 dark:text-blue-300 bg-white/90 dark:bg-stone-900/80 px-1.5 rounded shadow-sm border border-blue-200 dark:border-blue-800 whitespace-nowrap pointer-events-none">
                  {node.name}
                </div>
                <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-[8px] font-bold text-blue-500 dark:text-blue-400 bg-white/80 dark:bg-stone-900/80 px-1 rounded whitespace-nowrap pointer-events-none">
                  {vlanLabel}
                </div>
              </div>
            );
          }

          // Regular device node rendering
          const deviceNode = node as import('../types').DeviceNode;
          const status = runtimeStates[node.id];
          const isRouter = isDeviceNode(node) && deviceNode.type === DeviceType.ROUTER;
          const isSwitch = isDeviceNode(node) && deviceNode.type === DeviceType.SWITCH;

          let borderRadius = '8px';
          if (isRouter) borderRadius = '50%';
          if (isSwitch) borderRadius = '4px';

          // Status indicator: green=running, gray=stopped, yellow=booting, red=error, no dot=undeployed
          const getStatusDot = () => {
            if (!status) return null; // No status = undeployed, no indicator
            let dotColor = '#a8a29e'; // stone-400 (stopped)
            let animate = false;
            if (status === 'running') dotColor = '#22c55e'; // green-500
            else if (status === 'booting') { dotColor = '#eab308'; animate = true; } // yellow-500
            else if (status === 'error') dotColor = '#ef4444'; // red-500
            return (
              <div
                className={`absolute -top-1 -right-1 w-3 h-3 rounded-full border-2 border-white dark:border-stone-800 shadow-sm ${animate ? 'animate-pulse' : ''}`}
                style={{ backgroundColor: dotColor, transition: 'background-color 300ms ease-in-out' }}
                title={status}
              />
            );
          };

          return (
            <div
              key={node.id}
              style={{ left: node.x - 24, top: node.y - 24, borderRadius }}
              onMouseDown={(e) => handleNodeMouseDown(e, node.id)}
              onMouseUp={(e) => handleNodeMouseUp(e, node.id)}
              onContextMenu={(e) => handleNodeContextMenu(e, node.id)}
              className={`absolute w-12 h-12 flex items-center justify-center cursor-pointer shadow-sm transition-[box-shadow,background-color,border-color,transform] duration-150
                ${selectedId === node.id ? 'ring-2 ring-sage-500 bg-sage-500/10 dark:bg-sage-900/40 shadow-lg shadow-sage-500/20' : 'bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-600'}
                ${status === 'running' ? 'border-green-500/50 shadow-md shadow-green-500/10' : ''}
                ${linkingNode === node.id ? 'ring-2 ring-sage-400 scale-110' : ''}
                hover:border-sage-400 z-10 select-none group`}
            >
              <i className={`fa-solid ${getNodeIcon(deviceNode.model)} ${status === 'running' ? 'text-green-500 dark:text-green-400' : 'text-stone-700 dark:text-stone-100'} ${isRouter || isSwitch ? 'text-xl' : 'text-lg'}`}></i>
              {getStatusDot()}
              <div className="absolute top-full mt-1 text-[10px] font-bold text-stone-700 dark:text-stone-300 bg-white/90 dark:bg-stone-900/80 px-1 rounded shadow-sm border border-stone-200 dark:border-stone-700 whitespace-nowrap pointer-events-none">
                {node.name}
              </div>
            </div>
          );
        })}
      </div>

      <div className="absolute bottom-6 left-6 flex flex-col gap-2 z-30">
        <div className="bg-white/80 dark:bg-stone-900/80 backdrop-blur-md border border-stone-200 dark:border-stone-700 rounded-lg flex flex-col overflow-hidden shadow-lg">
          <button onClick={() => setZoom(prev => Math.min(prev * 1.2, 5))} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors border-b border-stone-200 dark:border-stone-700"><i className="fa-solid fa-plus"></i></button>
          <button onClick={() => setZoom(prev => Math.max(prev / 1.2, 0.1))} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"><i className="fa-solid fa-minus"></i></button>
        </div>
        <div className="bg-white/80 dark:bg-stone-900/80 backdrop-blur-md border border-stone-200 dark:border-stone-700 rounded-lg flex flex-col overflow-hidden shadow-lg">
          <button onClick={centerCanvas} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors border-b border-stone-200 dark:border-stone-700"><i className="fa-solid fa-crosshairs"></i></button>
          <button onClick={fitToScreen} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"><i className="fa-solid fa-maximize"></i></button>
        </div>
      </div>

      {contextMenu && (
        <div className="fixed z-[100] w-52 bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded-xl shadow-2xl py-2 animate-in fade-in zoom-in duration-100" style={{ left: contextMenu.x, top: contextMenu.y }} onMouseDown={(e) => e.stopPropagation()}>
          <div className="px-4 py-2 border-b border-stone-100 dark:border-stone-800 mb-1 flex items-center justify-between">
            <span className="text-[10px] font-black text-stone-400 dark:text-stone-500 uppercase tracking-widest">
              {contextMenu.type === 'node'
                ? (isExternalNetworkNode(nodeMap.get(contextMenu.id)!) ? 'External Network' : 'Node Actions')
                : 'Link Actions'}
            </span>
          </div>
          {contextMenu.type === 'node' && (() => {
            const contextNode = nodeMap.get(contextMenu.id);
            // External network nodes only have delete action
            if (contextNode && isExternalNetworkNode(contextNode)) {
              return null;
            }
            const nodeStatus = runtimeStates[contextMenu.id] || 'stopped';
            const hasRunningNodes = nodes.some(n => {
              const s = runtimeStates[n.id];
              return s === 'running' || s === 'booting';
            });
            const isNodeRunning = nodeStatus === 'running' || nodeStatus === 'booting';
            return (
              <>
                <button onClick={() => handleAction('console')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-stone-700 dark:text-stone-300 hover:bg-sage-600 hover:text-white transition-colors">
                  <i className="fa-solid fa-terminal w-4"></i> Open Console
                </button>
                {!isNodeRunning && (
                  <button onClick={() => handleAction('start')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-green-600 dark:text-green-400 hover:bg-green-600 hover:text-white transition-colors">
                    <i className={`fa-solid ${hasRunningNodes ? 'fa-play' : 'fa-rocket'} w-4`}></i> {hasRunningNodes ? 'Start Node' : 'Deploy Lab'}
                  </button>
                )}
                {isNodeRunning && (
                  <button onClick={() => handleAction('stop')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-red-600 dark:text-red-400 hover:bg-red-600 hover:text-white transition-colors">
                    <i className="fa-solid fa-power-off w-4"></i> Stop Node
                  </button>
                )}
                <div className="h-px bg-stone-100 dark:bg-stone-800 my-1 mx-2"></div>
              </>
            );
          })()}
          <button onClick={() => handleAction('delete')} className="w-full flex items-center gap-3 px-4 py-2 text-xs text-red-600 dark:text-red-500 hover:bg-red-600 hover:text-white transition-colors">
            <i className="fa-solid fa-trash-can w-4"></i>
            {contextMenu.type === 'node'
              ? (nodeMap.get(contextMenu.id) && isExternalNetworkNode(nodeMap.get(contextMenu.id)!)
                  ? 'Remove External Network'
                  : 'Remove Device')
              : 'Delete Connection'}
          </button>
        </div>
      )}
    </div>
  );
};

export default memo(Canvas);
