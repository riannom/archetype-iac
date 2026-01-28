import React from 'react';

export type StatusIndicatorStatus =
  | 'online'
  | 'offline'
  | 'warning'
  | 'running'
  | 'stopped'
  | 'pending'
  | 'error'
  | 'connecting';

export type StatusIndicatorSize = 'sm' | 'md' | 'lg';

export interface StatusIndicatorProps {
  status: StatusIndicatorStatus;
  size?: StatusIndicatorSize;
  pulse?: boolean;
  className?: string;
}

const statusColors: Record<StatusIndicatorStatus, string> = {
  online: 'bg-green-500',
  running: 'bg-green-500',
  warning: 'bg-amber-500',
  pending: 'bg-amber-500',
  connecting: 'bg-blue-500',
  offline: 'bg-red-500',
  error: 'bg-red-500',
  stopped: 'bg-stone-400 dark:bg-stone-600',
};

const sizeStyles: Record<StatusIndicatorSize, string> = {
  sm: 'w-2 h-2',
  md: 'w-3 h-3',
  lg: 'w-4 h-4',
};

const shouldPulseByDefault: Record<StatusIndicatorStatus, boolean> = {
  online: true,
  running: true,
  warning: false,
  pending: true,
  connecting: true,
  offline: false,
  error: false,
  stopped: false,
};

export const StatusIndicator: React.FC<StatusIndicatorProps> = ({
  status,
  size = 'md',
  pulse,
  className = '',
}) => {
  const shouldPulse = pulse ?? shouldPulseByDefault[status];

  return (
    <div
      className={`
        rounded-full
        ${statusColors[status]}
        ${sizeStyles[size]}
        ${shouldPulse ? 'animate-pulse' : ''}
        ${className}
      `.trim().replace(/\s+/g, ' ')}
    />
  );
};

export default StatusIndicator;
