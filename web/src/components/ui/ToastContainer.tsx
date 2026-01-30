import React from 'react';
import { createPortal } from 'react-dom';
import { useNotifications } from '../../contexts/NotificationContext';
import { Toast } from './Toast';

const positionClasses: Record<string, string> = {
  'bottom-right': 'bottom-4 right-4',
  'bottom-left': 'bottom-4 left-4',
  'top-right': 'top-4 right-4',
  'top-left': 'top-4 left-4',
};

export function ToastContainer() {
  const { toasts, dismissToast, preferences } = useNotifications();

  if (!preferences?.notification_settings.toasts.enabled) return null;

  const position = preferences.notification_settings.toasts.position;
  const positionClass = positionClasses[position] || positionClasses['bottom-right'];

  return createPortal(
    <div className={`fixed ${positionClass} z-[100] flex flex-col gap-2`}>
      {toasts.map((toast) => (
        <Toast
          key={toast.id}
          level={toast.level}
          title={toast.title}
          message={toast.message}
          onDismiss={() => dismissToast(toast.id)}
        />
      ))}
    </div>,
    document.body
  );
}

export default ToastContainer;
