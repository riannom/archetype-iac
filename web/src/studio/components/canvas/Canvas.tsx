
import React, { useRef, useState, useEffect, useMemo, memo } from 'react';
import { Node, DeviceType, isExternalNetworkNode, isDeviceNode } from '../../types';
import { useTheme } from '../../../theme/index';
import { getAgentColor, getAgentInitials } from '../../../utils/agentColors';
import { useNotifications } from '../../../contexts/NotificationContext';
import { CanvasProps, ContextMenu, ResizeHandle } from './types';
import { useCanvasViewport } from './useCanvasViewport';
import { useCanvasInteraction } from './useCanvasInteraction';
import { CanvasControls } from './CanvasControls';
import { ContextMenuOverlay } from './ContextMenuOverlay';

const Canvas: React.FC<CanvasProps> = ({
  nodes, links, annotations, runtimeStates, nodeStates = {}, linkStates, scenarioHighlights, deviceModels, labId, agents = [], showAgentIndicators = false, onToggleAgentIndicators, activeTool = 'pointer', onToolCreate, onNodeMove, onAnnotationMove, onConnect, selectedId, onSelect, onOpenConsole, onExtractConfig, onUpdateStatus, onDelete, onDropDevice, onDropExternalNetwork, onUpdateAnnotation, selectedIds, onSelectMultiple, focusNodeId, onFocusHandled
}) => {
  const { effectiveMode } = useTheme();
  const { preferences } = useNotifications();
  const errorIndicatorSettings = preferences?.canvas_settings.errorIndicator;
  const containerRef = useRef<HTMLDivElement>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null);
  const [hoveredLinkId, setHoveredLinkId] = useState<string | null>(null);

  const { zoom, setZoom, offset, setOffset, centerCanvas, fitToScreen } = useCanvasViewport({
    labId,
    nodes,
    annotations,
    containerRef,
    focusNodeId,
    onFocusHandled,
  });

  const interaction = useCanvasInteraction({
    containerRef,
    zoom,
    setZoom,
    offset,
    setOffset,
    nodes,
    annotations,
    activeTool,
    onToolCreate,
    onNodeMove,
    onAnnotationMove,
    onConnect,
    onSelect,
    onSelectMultiple,
    onUpdateAnnotation,
    onDropDevice,
    onDropExternalNetwork,
  });

  // Elapsed timer: re-render every second when any node is in a transitional state
  const hasTransitionalNodes = useMemo(() => {
    return Object.values(nodeStates).some(ns => {
      const ds = ns.display_state;
      return ds === 'starting' || ds === 'stopping';
    });
  }, [nodeStates]);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!hasTransitionalNodes) return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [hasTransitionalNodes]);

  // Memoized node map for O(1) lookups instead of O(n) .find() calls
  const nodeMap = useMemo(() => {
    const map = new Map<string, Node>();
    nodes.forEach(node => map.set(node.id, node));
    return map;
  }, [nodes]);


  // Build set of highlighted node names from scenario execution
  const highlightedNodeNames = scenarioHighlights?.activeNodeNames;

  // Parse scenario activeLinkName into node name pair for matching
  const highlightedLinkNodes = useMemo(() => {
    if (!scenarioHighlights?.activeLinkName) return null;
    const parts = scenarioHighlights.activeLinkName.split(' <-> ');
    if (parts.length !== 2) return null;
    return {
      a: parts[0].trim().split(':')[0],
      b: parts[1].trim().split(':')[0],
    };
  }, [scenarioHighlights?.activeLinkName]);

  // Build a lookup map from node name pairs to link actual_state for state-based coloring
  const linkActualStateMap = useMemo(() => {
    const map = new Map<string, string>();
    if (!linkStates) return map;
    linkStates.forEach((ls) => {
      // Key by both orderings so either direction matches
      map.set(`${ls.source_node}|${ls.target_node}`, ls.actual_state);
      map.set(`${ls.target_node}|${ls.source_node}`, ls.actual_state);
    });
    return map;
  }, [linkStates]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (interaction.editingText) return; // Don't delete while editing text
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const target = e.target as HTMLElement;
        if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA') {
          if (selectedIds && selectedIds.size > 0) {
            selectedIds.forEach(id => onDelete(id));
          } else if (selectedId) {
            onDelete(selectedId);
          }
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedId, selectedIds, onDelete, interaction.editingText]);

  // Enter inline edit mode when a text annotation is just created
  useEffect(() => {
    if (!interaction.pendingTextEditRef.current || !selectedId) return;
    const ann = annotations.find(a => a.id === selectedId && a.type === 'text');
    if (ann) {
      interaction.pendingTextEditRef.current = false;
      interaction.textEditCommittedRef.current = false;
      interaction.setEditingText({ id: ann.id, x: ann.x, y: ann.y });
    }
  }, [selectedId, annotations]);

  useEffect(() => {
    const handleClickOutside = () => setContextMenu(null);
    window.addEventListener('click', handleClickOutside);
    return () => window.removeEventListener('click', handleClickOutside);
  }, []);

  const handleNodeContextMenu = (e: React.MouseEvent, id: string) => {
    if (activeTool === 'hand') return;
    e.preventDefault();
    e.stopPropagation();
    onSelect(id);
    setContextMenu({ x: e.clientX, y: e.clientY, id, type: 'node' });
  };

  const handleLinkContextMenu = (e: React.MouseEvent, id: string) => {
    if (activeTool === 'hand') return;
    e.preventDefault();
    e.stopPropagation();
    onSelect(id);
    setContextMenu({ x: e.clientX, y: e.clientY, id, type: 'link' });
  };

  const getNodeIcon = (modelId: string) => deviceModels.find(m => m.id === modelId)?.icon || 'fa-arrows-to-dot';

  const handleAction = (action: string) => {
    if (contextMenu) {
      switch (action) {
        case 'delete': onDelete(contextMenu.id); break;
        case 'console': onOpenConsole(contextMenu.id); break;
        case 'extract-config': onExtractConfig?.(contextMenu.id); break;
        case 'start': onUpdateStatus(contextMenu.id, 'booting'); break;
        case 'stop': onUpdateStatus(contextMenu.id, 'stopped'); break;
        case 'reload': onUpdateStatus(contextMenu.id, 'booting'); break;
      }
      setContextMenu(null);
    }
  };

  const handleCanvasMouseDown = (e: React.MouseEvent) => {
    setContextMenu(null);
    interaction.handleMouseDown(e);
  };

  return (
    <div
      ref={containerRef}
      className={`flex-1 relative overflow-hidden canvas-grid ${
        effectiveMode === 'dark' ? 'bg-stone-950' : 'bg-stone-50'
      } ${interaction.isPanning ? 'cursor-grabbing' : activeTool === 'hand' ? 'cursor-grab' : activeTool === 'text' ? 'cursor-text' : activeTool !== 'pointer' ? 'cursor-crosshair' : 'cursor-default'}`}
      onMouseMove={interaction.handleMouseMove}
      onMouseUp={interaction.handleMouseUp}
      onMouseDown={handleCanvasMouseDown}
      onWheel={interaction.handleWheel}
      onDragOver={interaction.handleDragOver}
      onDrop={interaction.handleDrop}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div
        className="absolute inset-0 origin-top-left"
        style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})` }}
      >
        <svg className="absolute inset-0 w-[5000px] h-[5000px] pointer-events-none" style={{ overflow: 'visible' }}>
          {[...annotations].sort((a, b) => (a.zIndex ?? 0) - (b.zIndex ?? 0)).map(ann => {
            const isSelected = selectedId === ann.id || selectedIds?.has(ann.id) === true;
            const stroke = isSelected ? (effectiveMode === 'dark' ? '#65A30D' : '#4D7C0F') : (ann.color || (effectiveMode === 'dark' ? '#57534E' : '#D6D3D1'));
            const handleSize = 8;
            const handleFill = effectiveMode === 'dark' ? '#65A30D' : '#4D7C0F';

            // Render resize handles for rect
            const renderRectHandles = () => {
              if (!isSelected || ann.type !== 'rect') return null;
              const w = ann.width || 100;
              const h = ann.height || 60;
              const handles: { handle: ResizeHandle; cx: number; cy: number }[] = [
                { handle: 'nw', cx: ann.x, cy: ann.y },
                { handle: 'n', cx: ann.x + w / 2, cy: ann.y },
                { handle: 'ne', cx: ann.x + w, cy: ann.y },
                { handle: 'e', cx: ann.x + w, cy: ann.y + h / 2 },
                { handle: 'se', cx: ann.x + w, cy: ann.y + h },
                { handle: 's', cx: ann.x + w / 2, cy: ann.y + h },
                { handle: 'sw', cx: ann.x, cy: ann.y + h },
                { handle: 'w', cx: ann.x, cy: ann.y + h / 2 },
              ];
              return handles.map(({ handle, cx, cy }) => (
                <rect
                  key={handle}
                  x={cx - handleSize / 2}
                  y={cy - handleSize / 2}
                  width={handleSize}
                  height={handleSize}
                  fill={handleFill}
                  stroke={effectiveMode === 'dark' ? '#1C1917' : 'white'}
                  strokeWidth="1"
                  style={{ cursor: interaction.getResizeCursor(handle) }}
                  onMouseDown={(e) => interaction.handleResizeMouseDown(e, ann, handle)}
                />
              ));
            };

            // Render resize handles for circle (4 cardinal points)
            const renderCircleHandles = () => {
              if (!isSelected || ann.type !== 'circle') return null;
              const r = ann.width ? ann.width / 2 : 40;
              const handles: { handle: ResizeHandle; cx: number; cy: number }[] = [
                { handle: 'n', cx: ann.x, cy: ann.y - r },
                { handle: 'e', cx: ann.x + r, cy: ann.y },
                { handle: 's', cx: ann.x, cy: ann.y + r },
                { handle: 'w', cx: ann.x - r, cy: ann.y },
              ];
              return handles.map(({ handle, cx, cy }) => (
                <rect
                  key={handle}
                  x={cx - handleSize / 2}
                  y={cy - handleSize / 2}
                  width={handleSize}
                  height={handleSize}
                  fill={handleFill}
                  stroke={effectiveMode === 'dark' ? '#1C1917' : 'white'}
                  strokeWidth="1"
                  style={{ cursor: interaction.getResizeCursor(handle) }}
                  onMouseDown={(e) => interaction.handleResizeMouseDown(e, ann, handle)}
                />
              ));
            };

            // Render arrow endpoint handles (start = 'n', end = 's')
            const renderArrowHandles = () => {
              if (!isSelected || ann.type !== 'arrow') return null;
              const tx = ann.targetX ?? ann.x + 100;
              const ty = ann.targetY ?? ann.y + 100;
              const endpoints: { handle: ResizeHandle; cx: number; cy: number }[] = [
                { handle: 'n', cx: ann.x, cy: ann.y },
                { handle: 's', cx: tx, cy: ty },
              ];
              return endpoints.map(({ handle, cx, cy }) => (
                <circle
                  key={handle}
                  cx={cx}
                  cy={cy}
                  r={handleSize / 2 + 1}
                  fill={handleFill}
                  stroke={effectiveMode === 'dark' ? '#1C1917' : 'white'}
                  strokeWidth="1"
                  style={{ cursor: 'move' }}
                  onMouseDown={(e) => interaction.handleResizeMouseDown(e, ann, handle)}
                />
              ));
            };

            // Render arrow SVG
            const renderArrow = () => {
              if (ann.type !== 'arrow') return null;
              const tx = ann.targetX ?? ann.x + 100;
              const ty = ann.targetY ?? ann.y + 100;
              const dx = tx - ann.x;
              const dy = ty - ann.y;
              const len = Math.sqrt(dx * dx + dy * dy);
              if (len < 1) return null;
              const ux = dx / len;
              const uy = dy / len;
              const headLen = 12;
              const headW = 6;
              // Arrowhead at target end
              const tipX = tx;
              const tipY = ty;
              const baseX = tx - ux * headLen;
              const baseY = ty - uy * headLen;
              const leftX = baseX - uy * headW;
              const leftY = baseY + ux * headW;
              const rightX = baseX + uy * headW;
              const rightY = baseY - ux * headW;
              return (
                <>
                  <line x1={ann.x} y1={ann.y} x2={baseX} y2={baseY} stroke={stroke} strokeWidth="2" strokeDasharray={isSelected ? "4" : "0"} />
                  <polygon points={`${tipX},${tipY} ${leftX},${leftY} ${rightX},${rightY}`} fill={stroke} />
                </>
              );
            };

            const handleTextDoubleClick = (e: React.MouseEvent) => {
              if (ann.type === 'text') {
                e.stopPropagation();
                interaction.textEditCommittedRef.current = false;
                interaction.setEditingText({ id: ann.id, x: ann.x, y: ann.y });
              }
            };

            return (
              <g key={ann.id} className="pointer-events-auto cursor-move" onMouseDown={(e) => interaction.handleAnnotationMouseDown(e, ann.id)} onDoubleClick={handleTextDoubleClick}>
                {ann.type === 'rect' && <rect x={ann.x} y={ann.y} width={ann.width || 100} height={ann.height || 60} fill={effectiveMode === 'dark' ? "rgba(68, 64, 60, 0.2)" : "rgba(214, 211, 209, 0.2)"} stroke={stroke} strokeWidth="2" strokeDasharray={isSelected ? "4" : "0"} rx="4" />}
                {ann.type === 'circle' && <circle cx={ann.x} cy={ann.y} r={ann.width ? ann.width / 2 : 40} fill={effectiveMode === 'dark' ? "rgba(68, 64, 60, 0.2)" : "rgba(214, 211, 209, 0.2)"} stroke={stroke} strokeWidth="2" strokeDasharray={isSelected ? "4" : "0"} />}
                {ann.type === 'text' && !(interaction.editingText?.id === ann.id) && (() => {
                  const text = ann.text || 'New Text';
                  const fontSize = ann.fontSize || 14;
                  const pad = 4;
                  const approxW = Math.max(20, text.length * fontSize * 0.6);
                  const approxH = fontSize * 1.2;
                  return (
                    <>
                      {isSelected && <rect x={ann.x - pad} y={ann.y - approxH - pad} width={approxW + pad * 2} height={approxH + pad * 2} fill="none" stroke={handleFill} strokeWidth="1.5" strokeDasharray="4" rx="3" />}
                      <text x={ann.x} y={ann.y} fill={ann.color || (effectiveMode === 'dark' ? 'white' : '#1C1917')} fontSize={fontSize} className="font-black tracking-tight select-none">{text}</text>
                    </>
                  );
                })()}
                {renderArrow()}
                {renderRectHandles()}
                {renderCircleHandles()}
                {renderArrowHandles()}
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

            // Override with state-based color when link is not selected/hovered
            if (!isSelected && !isHovered && !isExternalLink) {
              const sourceName = source.name;
              const targetName = target.name;
              const actualState = linkActualStateMap.get(`${sourceName}|${targetName}`);
              if (actualState) {
                const stateColors: Record<string, { dark: string; light: string }> = {
                  up: { dark: '#22c55e', light: '#16a34a' },
                  error: { dark: '#ef4444', light: '#dc2626' },
                  pending: { dark: '#f59e0b', light: '#d97706' },
                  creating: { dark: '#f59e0b', light: '#d97706' },
                };
                const colors = stateColors[actualState];
                if (colors) {
                  linkColor = effectiveMode === 'dark' ? colors.dark : colors.light;
                }
                // down/unknown → keep default stone color (no change)
              }
            }

            // Scenario highlight: check if this link matches the active scenario step
            const isScenarioHighlighted = highlightedLinkNodes && (
              (source.name === highlightedLinkNodes.a && target.name === highlightedLinkNodes.b) ||
              (source.name === highlightedLinkNodes.b && target.name === highlightedLinkNodes.a)
            );

            return (
              <g key={link.id} className="pointer-events-auto cursor-pointer">
                <line x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="transparent" strokeWidth="12" onMouseDown={(e) => interaction.handleLinkMouseDown(e, link.id)} onContextMenu={(e) => handleLinkContextMenu(e, link.id)} onMouseEnter={() => setHoveredLinkId(link.id)} onMouseLeave={() => setHoveredLinkId(null)} />
                {isScenarioHighlighted && (
                  <line
                    x1={source.x} y1={source.y} x2={target.x} y2={target.y}
                    stroke={effectiveMode === 'dark' ? '#3B82F6' : '#2563EB'}
                    strokeWidth="6"
                    strokeOpacity="0.4"
                    className="animate-pulse"
                  />
                )}
                <line
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={linkColor}
                  strokeWidth={isSelected || isHovered ? "3" : "2"}
                  strokeDasharray={isExternalLink ? "6 4" : undefined}
                  className={interaction.draggingNode ? '' : 'transition-[stroke,stroke-width] duration-150'}
                />
                {(() => {
                  const t = 0.2;
                  const labelColor = effectiveMode === 'dark' ? '#E7E5E4' : '#44403C';
                  const labelStroke = effectiveMode === 'dark' ? '#1C1917' : '#FFFFFF';
                  return (
                    <>
                      {link.sourceInterface && (
                        <text x={source.x + t * (target.x - source.x)} y={source.y + t * (target.y - source.y)} fill={labelColor} stroke={labelStroke} strokeWidth="3" paintOrder="stroke" fontSize="11" fontWeight="700" textAnchor="middle" dominantBaseline="middle" className="select-none pointer-events-none" style={{ fontFamily: 'ui-monospace, monospace' }}>{link.sourceInterface}</text>
                      )}
                      {link.targetInterface && (
                        <text x={target.x + t * (source.x - target.x)} y={target.y + t * (source.y - target.y)} fill={labelColor} stroke={labelStroke} strokeWidth="3" paintOrder="stroke" fontSize="11" fontWeight="700" textAnchor="middle" dominantBaseline="middle" className="select-none pointer-events-none" style={{ fontFamily: 'ui-monospace, monospace' }}>{link.targetInterface}</text>
                      )}
                    </>
                  );
                })()}
              </g>
            );
          })}

          {interaction.linkingNode && (
            <line
              x1={nodeMap.get(interaction.linkingNode)?.x}
              y1={nodeMap.get(interaction.linkingNode)?.y}
              x2={interaction.mousePos.x}
              y2={interaction.mousePos.y}
              stroke="#65A30D"
              strokeWidth="2"
              strokeDasharray="4"
            />
          )}

          {/* Draw preview during tool gesture */}
          {interaction.drawStart && interaction.drawEnd && activeTool === 'rect' && (
            <rect
              x={Math.min(interaction.drawStart.x, interaction.drawEnd.x)}
              y={Math.min(interaction.drawStart.y, interaction.drawEnd.y)}
              width={Math.abs(interaction.drawEnd.x - interaction.drawStart.x)}
              height={Math.abs(interaction.drawEnd.y - interaction.drawStart.y)}
              fill="rgba(101, 163, 13, 0.1)"
              stroke="#65A30D"
              strokeWidth="2"
              strokeDasharray="6 3"
              rx="4"
            />
          )}
          {interaction.drawStart && interaction.drawEnd && activeTool === 'circle' && (() => {
            const r = Math.sqrt(Math.pow(interaction.drawEnd.x - interaction.drawStart.x, 2) + Math.pow(interaction.drawEnd.y - interaction.drawStart.y, 2));
            return (
              <circle
                cx={interaction.drawStart.x}
                cy={interaction.drawStart.y}
                r={r}
                fill="rgba(101, 163, 13, 0.1)"
                stroke="#65A30D"
                strokeWidth="2"
                strokeDasharray="6 3"
              />
            );
          })()}
          {interaction.drawStart && interaction.drawEnd && activeTool === 'arrow' && (() => {
            const dx = interaction.drawEnd.x - interaction.drawStart.x;
            const dy = interaction.drawEnd.y - interaction.drawStart.y;
            const len = Math.sqrt(dx * dx + dy * dy);
            if (len < 1) return null;
            const ux = dx / len;
            const uy = dy / len;
            const headLen = 12;
            const headW = 6;
            const baseX = interaction.drawEnd.x - ux * headLen;
            const baseY = interaction.drawEnd.y - uy * headLen;
            return (
              <>
                <line x1={interaction.drawStart.x} y1={interaction.drawStart.y} x2={baseX} y2={baseY} stroke="#65A30D" strokeWidth="2" strokeDasharray="6 3" />
                <polygon points={`${interaction.drawEnd.x},${interaction.drawEnd.y} ${baseX - uy * headW},${baseY + ux * headW} ${baseX + uy * headW},${baseY - ux * headW}`} fill="#65A30D" opacity="0.6" />
              </>
            );
          })()}

          {/* Marquee selection preview */}
          {interaction.marqueeStart && interaction.marqueeEnd && activeTool === 'pointer' && (
            <rect
              x={Math.min(interaction.marqueeStart.x, interaction.marqueeEnd.x)}
              y={Math.min(interaction.marqueeStart.y, interaction.marqueeEnd.y)}
              width={Math.abs(interaction.marqueeEnd.x - interaction.marqueeStart.x)}
              height={Math.abs(interaction.marqueeEnd.y - interaction.marqueeStart.y)}
              fill="rgba(59, 130, 246, 0.08)"
              stroke="#3B82F6"
              strokeWidth="1.5"
              strokeDasharray="6 3"
              rx="2"
            />
          )}
        </svg>

        {/* Inline text editing overlay */}
        {interaction.editingText && (() => {
          const ann = annotations.find(a => a.id === interaction.editingText!.id);
          if (!ann) return null;
          const fontSize = ann.fontSize || 14;
          return (
            <input
              ref={interaction.textInputRef}
              type="text"
              defaultValue={ann.text || ''}
              autoFocus
              className="absolute bg-transparent outline-none font-black tracking-tight"
              style={{
                left: ann.x,
                top: ann.y - fontSize,
                fontSize,
                color: ann.color || (effectiveMode === 'dark' ? 'white' : '#1C1917'),
                minWidth: 60,
                caretColor: effectiveMode === 'dark' ? '#84CC16' : '#65A30D',
                border: 'none',
                padding: 0,
                margin: 0,
                lineHeight: 1,
              }}
              onBlur={(e) => {
                if (interaction.textEditCommittedRef.current) return;
                interaction.textEditCommittedRef.current = true;
                const val = e.target.value.trim();
                if (val && onUpdateAnnotation) {
                  onUpdateAnnotation(interaction.editingText!.id, { text: val });
                } else if (!val) {
                  onDelete(interaction.editingText!.id);
                }
                interaction.setEditingText(null);
              }}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === 'Enter') {
                  (e.target as HTMLInputElement).blur();
                } else if (e.key === 'Escape') {
                  interaction.textEditCommittedRef.current = true;
                  const ann = annotations.find(a => a.id === interaction.editingText!.id);
                  if (!ann?.text) {
                    onDelete(interaction.editingText!.id);
                  }
                  interaction.setEditingText(null);
                }
              }}
              onMouseDown={(e) => e.stopPropagation()}
            />
          );
        })()}

        {nodes.map(node => {
          // Check if this is an external network node
          if (isExternalNetworkNode(node)) {
            // Render external network node with cloud shape
            const extNode = node;
            const vlanLabel = extNode.managedInterfaceName
              ? extNode.managedInterfaceName
              : extNode.connectionType === 'vlan'
                ? `VLAN ${extNode.vlanId || '?'}`
                : extNode.bridgeName || 'Unconfigured';

            return (
              <div
                key={node.id}
                style={{ left: node.x - 28, top: node.y - 20 }}
                onMouseDown={(e) => interaction.handleNodeMouseDown(e, node.id)}
                onMouseUp={(e) => interaction.handleNodeMouseUp(e, node.id)}
                onContextMenu={(e) => handleNodeContextMenu(e, node.id)}
                className={`absolute w-14 h-10 flex items-center justify-center cursor-pointer shadow-md transition-[box-shadow,background-color,border-color,transform] duration-150 rounded-2xl
                  ${(selectedId === node.id || selectedIds?.has(node.id))
                    ? 'ring-2 ring-blue-500 bg-gradient-to-br from-blue-100 to-purple-100 dark:from-blue-900/60 dark:to-purple-900/60 shadow-lg shadow-blue-500/20'
                    : 'bg-gradient-to-br from-blue-50 to-purple-50 dark:from-blue-950/40 dark:to-purple-950/40 border border-blue-300 dark:border-blue-700'}
                  ${interaction.linkingNode === node.id ? 'ring-2 ring-blue-400 scale-110' : ''}
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
          const deviceNode = node as import('../../types').DeviceNode;
          const status = runtimeStates[node.id];
          const isRouter = isDeviceNode(node) && deviceNode.type === DeviceType.ROUTER;
          const isSwitch = isDeviceNode(node) && deviceNode.type === DeviceType.SWITCH;

          let borderRadius = '8px';
          if (isRouter) borderRadius = '50%';
          if (isSwitch) borderRadius = '4px';

          // Status indicator: green=running, gray=stopped, yellow=booting, orange=stopping, red=error, no dot=undeployed
          const getStatusDot = () => {
            const ns = nodeStates?.[node.id];
            const imageSyncActive =
              ns?.image_sync_status === 'syncing' || ns?.image_sync_status === 'checking';
            if (!status && !imageSyncActive) return null; // No runtime state and no sync = undeployed
            let dotColor = '#a8a29e'; // stone-400 (stopped)
            let animate = false;
            if (status === 'running') dotColor = '#22c55e'; // green-500
            else if (status === 'booting') { dotColor = '#eab308'; animate = true; } // yellow-500
            else if (status === 'stopping') { dotColor = '#f97316'; animate = true; } // orange-500
            else if (status === 'error') dotColor = '#ef4444'; // red-500
            // Make image sync visually distinct from normal booting.
            if (imageSyncActive) {
              dotColor = '#3b82f6'; // blue-500
              animate = true;
            }

            // Build tooltip with retry info and elapsed time
            let tooltip: string = status || 'unknown';
            if (imageSyncActive) {
              const phase = ns?.image_sync_status === 'checking' ? 'checking' : 'syncing';
              tooltip = `image ${phase}`;
              if (ns?.image_sync_message) {
                tooltip += `: ${ns.image_sync_message}`;
              }
            }
            if (ns?.will_retry && (ns.enforcement_attempts ?? 0) > 0) {
              const max = ns.max_enforcement_attempts ?? 0;
              tooltip = max > 0
                ? `Starting (attempt ${ns.enforcement_attempts}/${max})`
                : `Starting (attempt ${ns.enforcement_attempts})`;
            }
            // Add elapsed time for transitional states
            if ((status === 'booting' || status === 'stopping') && ns?.starting_started_at) {
              const elapsed = Math.floor((Date.now() - new Date(ns.starting_started_at).getTime()) / 1000);
              if (elapsed > 0) {
                const mins = Math.floor(elapsed / 60);
                const secs = elapsed % 60;
                const timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
                tooltip += ` (${timeStr})`;
              }
            }

            return (
              <div
                className={`absolute -top-1 -right-1 w-3 h-3 rounded-full border-2 border-white dark:border-stone-800 shadow-sm ${animate ? 'animate-pulse' : ''}`}
                style={{ backgroundColor: dotColor, transition: 'background-color 300ms ease-in-out' }}
                title={tooltip}
              />
            );
          };

          // Agent indicator: shows which agent the node is running on
          const getAgentIndicator = () => {
            // Only show when enabled, multiple agents exist, and node has a host assigned
            if (!showAgentIndicators || agents.length <= 1) return null;
            const nodeState = nodeStates[node.id];
            if (!nodeState?.host_id || !nodeState?.host_name) return null;
            const color = getAgentColor(nodeState.host_id);
            const initials = getAgentInitials(nodeState.host_name);
            return (
              <div
                className="absolute -bottom-1 -left-1 w-4 h-4 rounded-full border-2 border-white dark:border-stone-800 shadow-sm flex items-center justify-center text-[7px] font-bold text-white"
                style={{ backgroundColor: color }}
                title={`Running on: ${nodeState.host_name}`}
              >
                {initials}
              </div>
            );
          };

          // Error indicator icon overlay
          const getErrorIndicator = () => {
            if (status !== 'error') return null;
            if (!errorIndicatorSettings?.showIcon) return null;
            const nodeState = nodeStates[node.id];
            const errorMessage = nodeState?.error_message || 'Node error';
            return (
              <div
                className="absolute -top-2 -right-2 w-5 h-5 bg-red-500 rounded-full flex items-center justify-center shadow-md cursor-pointer z-20"
                title={errorMessage}
              >
                <i className="fa-solid fa-exclamation text-white text-[10px]" />
              </div>
            );
          };

          // Error border class
          const errorBorderClass = status === 'error' && errorIndicatorSettings?.showBorder
            ? `ring-2 ring-red-500 ${errorIndicatorSettings?.pulseAnimation ? 'node-error-pulse' : ''}`
            : '';

          return (
            <div
              key={node.id}
              style={{ left: node.x - 24, top: node.y - 24, borderRadius }}
              onMouseDown={(e) => interaction.handleNodeMouseDown(e, node.id)}
              onMouseUp={(e) => interaction.handleNodeMouseUp(e, node.id)}
              onContextMenu={(e) => handleNodeContextMenu(e, node.id)}
              className={`absolute w-12 h-12 flex items-center justify-center cursor-pointer shadow-sm transition-[box-shadow,background-color,border-color,transform] duration-150
                ${(selectedId === node.id || selectedIds?.has(node.id)) ? 'ring-2 ring-sage-500 bg-sage-500/10 dark:bg-sage-900/40 shadow-lg shadow-sage-500/20' : 'bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-600'}
                ${status === 'running' ? 'border-green-500/50 shadow-md shadow-green-500/10' : ''}
                ${interaction.linkingNode === node.id ? 'ring-2 ring-sage-400 scale-110' : ''}
                ${errorBorderClass}
                hover:border-sage-400 z-10 select-none group`}
            >
              <div
                className={`flex items-center justify-center ${isRouter ? 'w-8 h-8 rounded-full' : 'w-8 h-8 rounded-md'} border ${
                  effectiveMode === 'dark'
                    ? 'bg-stone-700/40 border-stone-600/80'
                    : 'bg-stone-100/90 border-stone-400/90 shadow-[0_1px_2px_rgba(28,25,23,0.18)]'
                }`}
              >
                <i className={`fa-solid ${getNodeIcon(deviceNode.model)} ${status === 'running' ? 'text-green-500 dark:text-green-400' : status === 'error' ? 'text-red-500 dark:text-red-400' : 'text-stone-700 dark:text-stone-100'} ${isRouter || isSwitch ? 'text-xl' : 'text-lg'}`}></i>
              </div>
              {getStatusDot()}
              {getAgentIndicator()}
              {getErrorIndicator()}
              {highlightedNodeNames?.has(node.name) && (
                <div
                  className="absolute inset-[-6px] rounded-full border-2 border-blue-500 animate-pulse pointer-events-none"
                  style={{ borderRadius }}
                />
              )}
              <div className="absolute top-full mt-1 text-[10px] font-bold text-stone-700 dark:text-stone-300 bg-white/90 dark:bg-stone-900/80 px-1 rounded shadow-sm border border-stone-200 dark:border-stone-700 whitespace-nowrap pointer-events-none">
                {node.name}
              </div>
            </div>
          );
        })}

      </div>

      <CanvasControls
        setZoom={setZoom}
        centerCanvas={centerCanvas}
        fitToScreen={fitToScreen}
        agents={agents}
        showAgentIndicators={showAgentIndicators}
        onToggleAgentIndicators={onToggleAgentIndicators}
      />

      {contextMenu && (
        <ContextMenuOverlay
          contextMenu={contextMenu}
          nodeMap={nodeMap}
          runtimeStates={runtimeStates}
          onAction={handleAction}
        />
      )}
    </div>
  );
};

export default memo(Canvas);
