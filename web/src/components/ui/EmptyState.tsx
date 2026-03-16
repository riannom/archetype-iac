import React from 'react';

export interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
    icon?: string;
  };
  className?: string;
  compact?: boolean;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon = 'fa-solid fa-inbox',
  title,
  description,
  action,
  className = '',
  compact = false,
}) => {
  return (
    <div
      className={`
        flex flex-col items-center justify-center text-center
        ${compact ? 'py-8' : 'py-16'}
        bg-stone-100/30 dark:bg-stone-900/20
        border-2 border-dashed border-stone-300 dark:border-stone-800
        rounded-2xl
        animate-in fade-in duration-300
        ${className}
      `.trim().replace(/\s+/g, ' ')}
    >
      <div className="w-14 h-14 rounded-2xl bg-stone-200/50 dark:bg-stone-800/50 flex items-center justify-center mb-4">
        <i className={`${icon} text-2xl text-stone-400 dark:text-stone-600`} />
      </div>
      <h3 className="text-base font-semibold text-stone-600 dark:text-stone-400">
        {title}
      </h3>
      {description && (
        <p className="text-sm text-stone-500 dark:text-stone-500 mt-1.5 max-w-xs">
          {description}
        </p>
      )}
      {action && (
        <button
          onClick={action.onClick}
          className="mt-4 inline-flex items-center gap-2 px-4 py-2 bg-sage-600 hover:bg-sage-700 text-white text-sm font-semibold rounded-lg transition-all duration-150 active:scale-[0.97] shadow-sm hover:shadow-md"
        >
          {action.icon && <i className={action.icon} />}
          {action.label}
        </button>
      )}
    </div>
  );
};

export default EmptyState;
