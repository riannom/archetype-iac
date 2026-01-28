import React from 'react';

export interface PageLayoutProps {
  children: React.ReactNode;
  header?: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}

export const PageLayout: React.FC<PageLayoutProps> = ({
  children,
  header,
  footer,
  className = '',
}) => {
  return (
    <div
      className={`
        min-h-screen bg-stone-50 dark:bg-stone-900
        flex flex-col overflow-hidden
        ${className}
      `.trim().replace(/\s+/g, ' ')}
    >
      {header}
      <main className="flex-1 overflow-y-auto p-10 custom-scrollbar">
        {children}
      </main>
      {footer}
    </div>
  );
};

export interface PageContainerProps {
  children: React.ReactNode;
  maxWidth?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '7xl' | 'full';
  className?: string;
}

const maxWidthStyles: Record<NonNullable<PageContainerProps['maxWidth']>, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '7xl': 'max-w-7xl',
  full: 'max-w-full',
};

export const PageContainer: React.FC<PageContainerProps> = ({
  children,
  maxWidth = '7xl',
  className = '',
}) => {
  return (
    <div className={`${maxWidthStyles[maxWidth]} mx-auto ${className}`}>
      {children}
    </div>
  );
};

export interface PageTitleProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  className?: string;
}

export const PageTitle: React.FC<PageTitleProps> = ({
  title,
  description,
  actions,
  className = '',
}) => {
  return (
    <div className={`flex justify-between items-center mb-8 ${className}`}>
      <div>
        <h2 className="text-2xl font-bold text-stone-900 dark:text-white">
          {title}
        </h2>
        {description && (
          <p className="text-stone-500 text-sm mt-1">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex items-center gap-4">
          {actions}
        </div>
      )}
    </div>
  );
};

export interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon = 'fa-solid fa-inbox',
  title,
  description,
  action,
  className = '',
}) => {
  return (
    <div
      className={`
        py-20 bg-stone-100/50 dark:bg-stone-900/30
        border-2 border-dashed border-stone-300 dark:border-stone-800
        rounded-3xl flex flex-col items-center justify-center
        text-stone-500 dark:text-stone-600
        ${className}
      `.trim().replace(/\s+/g, ' ')}
    >
      <i className={`${icon} text-4xl mb-4`} />
      <p className="text-lg font-medium">{title}</p>
      {description && (
        <p className="text-sm mt-2">{description}</p>
      )}
      {action && (
        <div className="mt-4">
          {action}
        </div>
      )}
    </div>
  );
};

export interface LoadingStateProps {
  message?: string;
  className?: string;
}

export const LoadingState: React.FC<LoadingStateProps> = ({
  message = 'Loading...',
  className = '',
}) => {
  return (
    <div className={`flex items-center justify-center py-20 ${className}`}>
      <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl" />
      <span className="ml-3 text-stone-500">{message}</span>
    </div>
  );
};

export interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
  className?: string;
}

export const ErrorState: React.FC<ErrorStateProps> = ({
  message,
  onRetry,
  className = '',
}) => {
  return (
    <div className={`text-center py-20 text-red-500 ${className}`}>
      <i className="fa-solid fa-exclamation-circle text-3xl mb-3" />
      <p>{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 px-4 py-2 bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors text-sm font-medium"
        >
          Try Again
        </button>
      )}
    </div>
  );
};

export default PageLayout;
