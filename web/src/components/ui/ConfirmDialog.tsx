import React from 'react';
import { Modal, ModalFooter } from './Modal';
import { Button } from './Button';

export type ConfirmDialogVariant = 'danger' | 'warning' | 'info';

export interface ConfirmDialogProps {
  isOpen: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmDialogVariant;
  loading?: boolean;
}

const variantConfig: Record<ConfirmDialogVariant, { icon: string; iconColor: string; buttonVariant: 'danger' | 'primary' }> = {
  danger: {
    icon: 'fa-solid fa-triangle-exclamation',
    iconColor: 'text-red-500',
    buttonVariant: 'danger',
  },
  warning: {
    icon: 'fa-solid fa-circle-exclamation',
    iconColor: 'text-amber-500',
    buttonVariant: 'primary',
  },
  info: {
    icon: 'fa-solid fa-circle-info',
    iconColor: 'text-blue-500',
    buttonVariant: 'primary',
  },
};

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  isOpen,
  onConfirm,
  onCancel,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'danger',
  loading = false,
}) => {
  const config = variantConfig[variant];

  return (
    <Modal isOpen={isOpen} onClose={onCancel} size="sm" showCloseButton={false}>
      <div className="flex gap-4">
        <div className="flex-shrink-0 w-10 h-10 rounded-full bg-stone-100 dark:bg-stone-800 flex items-center justify-center">
          <i className={`${config.icon} ${config.iconColor} text-lg`} />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-base font-semibold text-stone-900 dark:text-stone-100">
            {title}
          </h3>
          <p className="mt-1.5 text-sm text-stone-600 dark:text-stone-400 whitespace-pre-line">
            {message}
          </p>
        </div>
      </div>
      <ModalFooter>
        <Button variant="ghost" onClick={onCancel} disabled={loading}>
          {cancelLabel}
        </Button>
        <Button variant={config.buttonVariant} onClick={onConfirm} loading={loading}>
          {confirmLabel}
        </Button>
      </ModalFooter>
    </Modal>
  );
};

export default ConfirmDialog;
