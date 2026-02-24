import React, { useCallback, useRef } from 'react';
import type { AgentGraphNode } from './types';

interface AgentNodeProps {
  agent: AgentGraphNode;
  x: number;
  y: number;
  isSelected: boolean;
  isDimmed: boolean;
  onSelect: (multi: boolean) => void;
  onDrag: (agentId: string, dx: number, dy: number) => void;
  onDragEnd: () => void;
  overflowCount?: number;
  onHoverEnter?: () => void;
  onHoverLeave?: () => void;
}

const AgentNode: React.FC<AgentNodeProps> = ({
  agent,
  x,
  y,
  isSelected,
  isDimmed,
  onSelect,
  onDrag,
  onDragEnd,
  overflowCount = 0,
  onHoverEnter,
  onHoverLeave,
}) => {
  const pointerDown = useRef(false);
  const dragging = useRef(false);
  const lastPointer = useRef({ x: 0, y: 0 });

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    e.stopPropagation();
    (e.target as SVGElement).setPointerCapture(e.pointerId);
    pointerDown.current = true;
    dragging.current = false;
    lastPointer.current = { x: e.clientX, y: e.clientY };
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!pointerDown.current) return;
    const dx = e.clientX - lastPointer.current.x;
    const dy = e.clientY - lastPointer.current.y;
    if (!dragging.current && Math.abs(dx) + Math.abs(dy) > 3) {
      dragging.current = true;
    }
    if (dragging.current) {
      lastPointer.current = { x: e.clientX, y: e.clientY };
      onDrag(agent.agentId, dx, dy);
    }
  }, [agent.agentId, onDrag]);

  const handlePointerUp = useCallback((e: React.PointerEvent) => {
    (e.target as SVGElement).releasePointerCapture(e.pointerId);
    if (!dragging.current && pointerDown.current) {
      onSelect(e.metaKey || e.ctrlKey || e.shiftKey);
    }
    pointerDown.current = false;
    dragging.current = false;
    onDragEnd();
  }, [onSelect, onDragEnd]);

  const mainOpacity = isDimmed ? 0.3 : 0.9;
  const innerOpacity = isDimmed ? 0.05 : 0.2;

  return (
    <g
      transform={`translate(${x}, ${y})`}
      style={{ cursor: 'grab' }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerEnter={onHoverEnter}
      onPointerLeave={onHoverLeave}
    >
      {/* Glow halo */}
      <circle
        r={28}
        fill={agent.color}
        opacity={isSelected ? 0.15 : 0}
        style={{ transition: 'opacity 300ms ease' }}
      />

      {/* Selection ring */}
      <circle
        r={22}
        fill="none"
        stroke={agent.color}
        strokeWidth={isSelected ? 2 : 0}
        opacity={isSelected ? 0.6 : 0}
        style={{ transition: 'all 300ms ease' }}
      />

      {/* Main dot */}
      <circle
        r={16}
        fill={agent.color}
        opacity={mainOpacity}
        style={{ transition: 'opacity 300ms ease' }}
      />

      {/* Inner highlight */}
      <circle
        r={6}
        fill="white"
        opacity={innerOpacity}
        style={{ transition: 'opacity 300ms ease' }}
      />

      {/* Agent name label */}
      <text
        y={30}
        textAnchor="middle"
        fill="#78716c"
        fontSize={11}
        fontFamily="'DM Sans', sans-serif"
        style={{ pointerEvents: 'none', userSelect: 'none' }}
      >
        {agent.agentName}
      </text>

      {/* Overflow badge */}
      {overflowCount > 0 && (
        <text
          y={44}
          textAnchor="middle"
          fill="#a8a29e"
          fontSize={9}
          fontFamily="'JetBrains Mono', monospace"
          style={{ pointerEvents: 'none', userSelect: 'none' }}
        >
          +{overflowCount} more
        </text>
      )}
    </g>
  );
};

export default AgentNode;
