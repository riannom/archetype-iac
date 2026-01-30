import React from 'react';
import type { NotificationLevel } from '../../types/notifications';

interface ToastProps {
  level: NotificationLevel;
  title: string;
  message?: string;
  onDismiss: () => void;
}

const levelStyles: Record<
  NotificationLevel,
  { bg: string; border: string; icon: string; iconColor: string }
> = {
  info: {
    bg: 'bg-blue-50 dark:bg-blue-900/30',
    border: 'border-blue-200 dark:border-blue-700',
    icon: 'fa-circle-info',
    iconColor: 'text-blue-500',
  },
  success: {
    bg: 'bg-green-50 dark:bg-green-900/30',
    border: 'border-green-200 dark:border-green-700',
    icon: 'fa-circle-check',
    iconColor: 'text-green-500',
  },
  warning: {
    bg: 'bg-amber-50 dark:bg-amber-900/30',
    border: 'border-amber-200 dark:border-amber-700',
    icon: 'fa-triangle-exclamation',
    iconColor: 'text-amber-500',
  },
  error: {
    bg: 'bg-red-50 dark:bg-red-900/30',
    border: 'border-red-200 dark:border-red-700',
    icon: 'fa-circle-xmark',
    iconColor: 'text-red-500',
  },
};

export function Toast({ level, title, message, onDismiss }: ToastProps) {
  const styles = levelStyles[level];

  return (
    <div
      className={`${styles.bg} ${styles.border} border rounded-lg shadow-lg p-4 max-w-sm animate-in slide-in-from-right fade-in duration-200`}
    >
      <div className="flex items-start gap-3">
        <i className={`fa-solid ${styles.icon} ${styles.iconColor} text-lg mt-0.5`} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-stone-900 dark:text-stone-100">{title}</p>
          {message && (
            <p className="text-xs text-stone-600 dark:text-stone-400 mt-1 line-clamp-2">
              {message}
            </p>
          )}
        </div>
        <button
          onClick={onDismiss}
          className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-200 transition-colors"
        >
          <i className="fa-solid fa-xmark" />
        </button>
      </div>
    </div>
  );
}

export default Toast;
