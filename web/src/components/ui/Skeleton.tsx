import React from 'react';

export type SkeletonVariant = 'text' | 'circular' | 'rectangular' | 'card';

export interface SkeletonProps {
  variant?: SkeletonVariant;
  width?: string | number;
  height?: string | number;
  className?: string;
  count?: number;
}

const variantStyles: Record<SkeletonVariant, string> = {
  text: 'h-4 rounded',
  circular: 'rounded-full',
  rectangular: 'rounded-lg',
  card: 'rounded-xl h-48',
};

export const Skeleton: React.FC<SkeletonProps> = ({
  variant = 'text',
  width,
  height,
  className = '',
  count = 1,
}) => {
  const style: React.CSSProperties = {};
  if (width) style.width = typeof width === 'number' ? `${width}px` : width;
  if (height) style.height = typeof height === 'number' ? `${height}px` : height;

  const items = Array.from({ length: count }, (_, i) => (
    <div
      key={i}
      className={`
        skeleton-shimmer bg-stone-200 dark:bg-stone-800
        ${variantStyles[variant]}
        ${!width && variant === 'text' ? 'w-full' : ''}
        ${variant === 'circular' && !width ? 'w-10 h-10' : ''}
        ${className}
      `.trim().replace(/\s+/g, ' ')}
      style={style}
    />
  ));

  if (count === 1) return items[0];

  return (
    <div className="flex flex-col gap-2">
      {items}
    </div>
  );
};

export function SkeletonCard() {
  return (
    <div className="glass-surface border rounded-2xl p-6 space-y-4">
      <Skeleton variant="rectangular" height={48} width={48} />
      <Skeleton variant="text" width="60%" />
      <Skeleton variant="text" width="40%" />
      <div className="pt-2">
        <Skeleton variant="text" count={2} />
      </div>
      <Skeleton variant="rectangular" height={36} />
    </div>
  );
}

export default Skeleton;
