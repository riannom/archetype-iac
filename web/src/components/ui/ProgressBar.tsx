import React from 'react';
import { defaultThresholds, getResourceLevel, type ResourceThresholds } from '../../utils/status';

export type ProgressBarVariant = 'default' | 'cpu' | 'memory' | 'storage';
export type ProgressBarSize = 'sm' | 'md' | 'lg';

export interface ProgressBarProps {
  value: number;
  variant?: ProgressBarVariant;
  size?: ProgressBarSize;
  thresholds?: ResourceThresholds;
  showLabel?: boolean;
  className?: string;
}

const variantColors: Record<ProgressBarVariant, Record<'normal' | 'warning' | 'danger', string>> = {
  default: {
    normal: 'bg-sage-500',
    warning: 'bg-amber-500',
    danger: 'bg-red-500',
  },
  cpu: {
    normal: 'bg-sage-500',
    warning: 'bg-amber-500',
    danger: 'bg-red-500',
  },
  memory: {
    normal: 'bg-blue-500',
    warning: 'bg-amber-500',
    danger: 'bg-red-500',
  },
  storage: {
    normal: 'bg-violet-500',
    warning: 'bg-amber-500',
    danger: 'bg-red-500',
  },
};

const variantThresholds: Record<ProgressBarVariant, ResourceThresholds> = {
  default: defaultThresholds.cpu,
  cpu: defaultThresholds.cpu,
  memory: defaultThresholds.memory,
  storage: defaultThresholds.storage,
};

const sizeStyles: Record<ProgressBarSize, string> = {
  sm: 'h-1',
  md: 'h-1.5',
  lg: 'h-2',
};

export const ProgressBar: React.FC<ProgressBarProps> = ({
  value,
  variant = 'default',
  size = 'md',
  thresholds,
  showLabel = false,
  className = '',
}) => {
  const effectiveThresholds = thresholds ?? variantThresholds[variant];
  const level = getResourceLevel(value, effectiveThresholds);
  const colorClass = variantColors[variant][level];
  const clampedValue = Math.min(Math.max(value, 0), 100);

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className={`flex-1 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden ${sizeStyles[size]}`}>
        <div
          className={`h-full ${colorClass} transition-all duration-500`}
          style={{ width: `${clampedValue}%` }}
        />
      </div>
      {showLabel && (
        <span className="text-xs font-semibold text-stone-600 dark:text-stone-400 min-w-[3ch] text-right">
          {Math.round(clampedValue)}%
        </span>
      )}
    </div>
  );
};

export default ProgressBar;
