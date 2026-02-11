import React from 'react';

export type CardVariant = 'default' | 'interactive' | 'selected';

export interface CardProps {
  variant?: CardVariant;
  children: React.ReactNode;
  className?: string;
  onClick?: () => void;
}

const variantStyles: Record<CardVariant, string> = {
  default: 'glass-surface',
  interactive: 'glass-surface hover:border-stone-300 dark:hover:border-stone-700 hover:shadow-sm cursor-pointer',
  selected: 'bg-sage-50 dark:bg-sage-900/20 border-sage-300 dark:border-sage-700 ring-1 ring-sage-200 dark:ring-sage-800',
};

export const Card: React.FC<CardProps> = ({
  variant = 'default',
  children,
  className = '',
  onClick,
}) => {
  const Component = onClick ? 'button' : 'div';

  return (
    <Component
      onClick={onClick}
      className={`
        rounded-xl border transition-all
        ${variantStyles[variant]}
        ${className}
      `.trim().replace(/\s+/g, ' ')}
    >
      {children}
    </Component>
  );
};

export interface CardHeaderProps {
  children: React.ReactNode;
  className?: string;
}

export const CardHeader: React.FC<CardHeaderProps> = ({ children, className = '' }) => (
  <div className={`px-4 py-3 border-b border-stone-200 dark:border-stone-800 ${className}`}>
    {children}
  </div>
);

export interface CardContentProps {
  children: React.ReactNode;
  className?: string;
}

export const CardContent: React.FC<CardContentProps> = ({ children, className = '' }) => (
  <div className={`p-4 ${className}`}>
    {children}
  </div>
);

export interface CardFooterProps {
  children: React.ReactNode;
  className?: string;
}

export const CardFooter: React.FC<CardFooterProps> = ({ children, className = '' }) => (
  <div className={`px-4 py-3 border-t border-stone-200 dark:border-stone-800 ${className}`}>
    {children}
  </div>
);

export default Card;
