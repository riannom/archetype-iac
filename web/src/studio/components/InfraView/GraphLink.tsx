import React, { useCallback } from 'react';
import type { LinkStateData } from '../../hooks/useLabStateWS';

interface GraphLinkProps {
  linkState: LinkStateData;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  isHighlighted: boolean;
  isDimmed: boolean;
  isSelected?: boolean;
  onSelect?: (linkName: string) => void;
}

const STATE_COLORS: Record<string, string> = {
  up: '#22c55e',
  error: '#ef4444',
  pending: '#f59e0b',
  down: '#57534e',
  unknown: '#57534e',
};

const GraphLink: React.FC<GraphLinkProps> = ({
  linkState,
  x1,
  y1,
  x2,
  y2,
  isHighlighted,
  isDimmed,
  isSelected,
  onSelect,
}) => {
  const stateColor = STATE_COLORS[linkState.actual_state] || STATE_COLORS.unknown;
  const baseOpacity = isDimmed ? 0.12 : isHighlighted ? 0.7 : 0.45;
  const opacity = isSelected ? 1 : baseOpacity;

  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;

  const handleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onSelect?.(linkState.link_name);
  }, [onSelect, linkState.link_name]);

  return (
    <g style={{ transition: 'opacity 300ms ease' }} opacity={opacity}>
      {/* Invisible wider hit area for click target */}
      <line
        x1={x1}
        y1={y1}
        x2={x2}
        y2={y2}
        stroke="transparent"
        strokeWidth={12}
        style={{ cursor: onSelect ? 'pointer' : 'default' }}
        onClick={handleClick}
      />

      {/* Visible dashed VNI link line */}
      <line
        x1={x1}
        y1={y1}
        x2={x2}
        y2={y2}
        stroke={stateColor}
        strokeWidth={isSelected ? 2 : 1}
        strokeDasharray="4 3"
        strokeLinecap="round"
        style={{ pointerEvents: 'none' }}
      >
        <animate
          attributeName="stroke-dashoffset"
          from="0"
          to="7"
          dur="1.2s"
          repeatCount="indefinite"
        />
      </line>

      {/* Selection glow */}
      {isSelected && (
        <line
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke={stateColor}
          strokeWidth={6}
          strokeLinecap="round"
          opacity={0.2}
          style={{ pointerEvents: 'none' }}
        />
      )}

      {/* VNI badge at midpoint */}
      {linkState.vni != null && (
        <g
          style={{ cursor: onSelect ? 'pointer' : 'default' }}
          onClick={handleClick}
        >
          <rect
            x={mx - 22}
            y={my - 8}
            width={44}
            height={16}
            rx={4}
            fill={isSelected ? 'rgba(99, 102, 241, 1)' : 'rgba(99, 102, 241, 0.85)'}
          />
          <text
            x={mx}
            y={my + 3}
            textAnchor="middle"
            fill="white"
            fontSize={9}
            fontWeight="bold"
            fontFamily="'JetBrains Mono', monospace"
            style={{ pointerEvents: 'none' }}
          >
            VNI {linkState.vni}
          </text>
        </g>
      )}
    </g>
  );
};

export default GraphLink;
