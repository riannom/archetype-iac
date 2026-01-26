import React from 'react';

interface FilterChipProps {
  label: string;
  isActive: boolean;
  onClick: () => void;
  count?: number;
  variant?: 'default' | 'status';
  statusColor?: 'green' | 'blue' | 'amber';
}

const FilterChip: React.FC<FilterChipProps> = ({
  label,
  isActive,
  onClick,
  count,
  variant = 'default',
  statusColor,
}) => {
  const getStatusDot = () => {
    if (variant !== 'status' || !statusColor) return null;
    const colors = {
      green: 'bg-emerald-500',
      blue: 'bg-blue-500',
      amber: 'bg-amber-500',
    };
    return (
      <span className={`w-2 h-2 rounded-full ${colors[statusColor]} mr-1.5`} />
    );
  };

  return (
    <button
      onClick={onClick}
      className={`
        inline-flex items-center px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wide
        transition-all duration-150 ease-out
        ${isActive
          ? 'bg-sage-600 text-white shadow-sm'
          : 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:bg-stone-200 dark:hover:bg-stone-700 hover:text-stone-800 dark:hover:text-stone-200'
        }
      `}
    >
      {getStatusDot()}
      <span>{label}</span>
      {count !== undefined && (
        <span className={`ml-1.5 px-1.5 py-0.5 rounded text-[9px] ${
          isActive
            ? 'bg-sage-700 text-sage-100'
            : 'bg-stone-200 dark:bg-stone-700 text-stone-500 dark:text-stone-400'
        }`}>
          {count}
        </span>
      )}
    </button>
  );
};

export default FilterChip;
