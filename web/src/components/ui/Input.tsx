import React from 'react';

export type InputSize = 'sm' | 'md' | 'lg';

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: string;
  error?: string;
  hint?: string;
  leftIcon?: string;
  rightIcon?: string;
  size?: InputSize;
}

const sizeStyles: Record<InputSize, string> = {
  sm: 'px-2.5 py-1.5 text-xs',
  md: 'px-3 py-2 text-sm',
  lg: 'px-4 py-2.5 text-base',
};

const iconSizeStyles: Record<InputSize, string> = {
  sm: 'text-xs',
  md: 'text-sm',
  lg: 'text-base',
};

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({
    label,
    error,
    hint,
    leftIcon,
    rightIcon,
    size = 'md',
    disabled,
    className = '',
    id,
    ...props
  }, ref) => {
    const inputId = id || (label ? `input-${label.toLowerCase().replace(/\s+/g, '-')}` : undefined);

    const inputStyles = `
      w-full rounded-lg border transition-all
      bg-white dark:bg-stone-800
      text-stone-900 dark:text-stone-100
      placeholder:text-stone-400 dark:placeholder:text-stone-500
      ${error
        ? 'border-red-300 dark:border-red-700 focus:ring-red-500 focus:border-red-500'
        : 'border-stone-300 dark:border-stone-700 focus:ring-sage-500 focus:border-sage-500'
      }
      focus:outline-none focus:ring-2 focus:ring-offset-0
      ${disabled ? 'opacity-50 cursor-not-allowed bg-stone-50 dark:bg-stone-900' : ''}
      ${leftIcon ? 'pl-9' : ''}
      ${rightIcon ? 'pr-9' : ''}
      ${sizeStyles[size]}
      ${className}
    `.trim().replace(/\s+/g, ' ');

    return (
      <div className="w-full">
        {label && (
          <label
            htmlFor={inputId}
            className="block text-xs font-semibold text-stone-600 dark:text-stone-400 uppercase tracking-wide mb-1.5"
          >
            {label}
          </label>
        )}
        <div className="relative">
          {leftIcon && (
            <div className="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
              <i className={`${leftIcon} ${iconSizeStyles[size]} text-stone-400 dark:text-stone-500`} />
            </div>
          )}
          <input
            ref={ref}
            id={inputId}
            disabled={disabled}
            className={inputStyles}
            {...props}
          />
          {rightIcon && (
            <div className="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none">
              <i className={`${rightIcon} ${iconSizeStyles[size]} text-stone-400 dark:text-stone-500`} />
            </div>
          )}
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

Input.displayName = 'Input';

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  hint?: string;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, error, hint, disabled, className = '', id, ...props }, ref) => {
    const textareaId = id || (label ? `textarea-${label.toLowerCase().replace(/\s+/g, '-')}` : undefined);

    const textareaStyles = `
      w-full px-3 py-2 text-sm rounded-lg border transition-all
      bg-white dark:bg-stone-800
      text-stone-900 dark:text-stone-100
      placeholder:text-stone-400 dark:placeholder:text-stone-500
      ${error
        ? 'border-red-300 dark:border-red-700 focus:ring-red-500 focus:border-red-500'
        : 'border-stone-300 dark:border-stone-700 focus:ring-sage-500 focus:border-sage-500'
      }
      focus:outline-none focus:ring-2 focus:ring-offset-0
      ${disabled ? 'opacity-50 cursor-not-allowed bg-stone-50 dark:bg-stone-900' : ''}
      resize-none
      ${className}
    `.trim().replace(/\s+/g, ' ');

    return (
      <div className="w-full">
        {label && (
          <label
            htmlFor={textareaId}
            className="block text-xs font-semibold text-stone-600 dark:text-stone-400 uppercase tracking-wide mb-1.5"
          >
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={textareaId}
          disabled={disabled}
          className={textareaStyles}
          {...props}
        />
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

Textarea.displayName = 'Textarea';

export default Input;
