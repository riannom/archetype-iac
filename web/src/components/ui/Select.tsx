import React from 'react';

export type SelectSize = 'sm' | 'md' | 'lg';

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  label?: string;
  error?: string;
  hint?: string;
  size?: SelectSize;
  options?: SelectOption[];
}

const sizeStyles: Record<SelectSize, string> = {
  sm: 'px-2.5 py-1.5 text-xs pr-8',
  md: 'px-3 py-2 text-sm pr-9',
  lg: 'px-4 py-2.5 text-base pr-10',
};

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({
    label,
    error,
    hint,
    size = 'md',
    disabled,
    className = '',
    id,
    options,
    children,
    ...props
  }, ref) => {
    const selectId = id || (label ? `select-${label.toLowerCase().replace(/\s+/g, '-')}` : undefined);

    const selectStyles = `
      w-full rounded-lg border transition-all duration-150 appearance-none cursor-pointer
      bg-white dark:bg-stone-800
      text-stone-900 dark:text-stone-100
      ${error
        ? 'border-red-300 dark:border-red-700 focus:ring-red-500 focus:border-red-500'
        : 'border-stone-300 dark:border-stone-700 focus:ring-sage-500 focus:border-sage-500'
      }
      focus:outline-none focus:ring-2 focus:ring-offset-0
      ${disabled ? 'opacity-50 cursor-not-allowed bg-stone-50 dark:bg-stone-900' : ''}
      ${sizeStyles[size]}
      ${className}
    `.trim().replace(/\s+/g, ' ');

    return (
      <div className="w-full">
        {label && (
          <label
            htmlFor={selectId}
            className="block text-xs font-semibold text-stone-600 dark:text-stone-400 uppercase tracking-wide mb-1.5"
          >
            {label}
          </label>
        )}
        <div className="relative">
          <select
            ref={ref}
            id={selectId}
            disabled={disabled}
            className={selectStyles}
            {...props}
          >
            {options
              ? options.map((opt) => (
                  <option key={opt.value} value={opt.value} disabled={opt.disabled}>
                    {opt.label}
                  </option>
                ))
              : children}
          </select>
          <div className="absolute inset-y-0 right-0 flex items-center pr-2.5 pointer-events-none">
            <i className="fa-solid fa-chevron-down text-[11px] text-stone-400 dark:text-stone-500" />
          </div>
        </div>
        {error && (
          <p className="mt-1 text-xs text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
        {hint && !error && (
          <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
            {hint}
          </p>
        )}
      </div>
    );
  }
);

Select.displayName = 'Select';

export default Select;
