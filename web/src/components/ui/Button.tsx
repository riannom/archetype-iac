import React from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  leftIcon?: string;
  rightIcon?: string;
  loading?: boolean;
  children?: React.ReactNode;
}

const variantStyles: Record<ButtonVariant, string> = {
  primary: 'bg-sage-600 hover:bg-sage-700 text-white border-sage-600 hover:border-sage-700 dark:bg-sage-600 dark:hover:bg-sage-500',
  secondary: 'bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-300 border-stone-300 dark:border-stone-700',
  ghost: 'bg-transparent hover:bg-stone-100 dark:hover:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-stone-200 border-transparent',
  danger: 'bg-red-600 hover:bg-red-700 text-white border-red-600 hover:border-red-700',
};

const sizeStyles: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1.5 text-xs gap-1.5',
  md: 'px-3 py-2 text-sm gap-2',
  lg: 'px-4 py-2.5 text-base gap-2.5',
  icon: 'w-9 h-9 p-0 justify-center',
};

const iconSizeStyles: Record<ButtonSize, string> = {
  sm: 'text-xs',
  md: 'text-sm',
  lg: 'text-base',
  icon: 'text-sm',
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({
    variant = 'secondary',
    size = 'md',
    leftIcon,
    rightIcon,
    loading = false,
    disabled,
    className = '',
    children,
    ...props
  }, ref) => {
    const isDisabled = disabled || loading;

    return (
      <button
        ref={ref}
        disabled={isDisabled}
        className={`
          inline-flex items-center font-semibold rounded-lg border transition-all
          ${variantStyles[variant]}
          ${sizeStyles[size]}
          ${isDisabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
          ${className}
        `.trim().replace(/\s+/g, ' ')}
        {...props}
      >
        {loading ? (
          <i className={`fa-solid fa-spinner fa-spin ${iconSizeStyles[size]}`} />
        ) : leftIcon ? (
          <i className={`${leftIcon} ${iconSizeStyles[size]}`} />
        ) : null}
        {size !== 'icon' && children}
        {!loading && rightIcon && (
          <i className={`${rightIcon} ${iconSizeStyles[size]}`} />
        )}
      </button>
    );
  }
);

Button.displayName = 'Button';

export default Button;
