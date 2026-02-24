import React from 'react';
import Modal from '../../components/ui/Modal';

interface ConfigRebootConfirmModalProps {
  isOpen: boolean;
  onClose: () => void;
  onRebootNow: () => void;
  onApplyLater: () => void;
  loading: boolean;
  actionDescription: string;
}

const ConfigRebootConfirmModal: React.FC<ConfigRebootConfirmModalProps> = ({
  isOpen,
  onClose,
  onRebootNow,
  onApplyLater,
  loading,
  actionDescription,
}) => {
  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Apply Configuration Change" size="sm">
      <div className="space-y-4">
        <p className="text-sm text-stone-600 dark:text-stone-400">
          {actionDescription}
        </p>
        <p className="text-sm text-stone-700 dark:text-stone-300">
          Would you like to reboot the node now to apply this configuration?
        </p>
        <div className="flex gap-3 pt-2">
          <button
            onClick={onRebootNow}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-sage-600 hover:bg-sage-500 disabled:opacity-50 text-white text-xs font-bold rounded-lg transition-all"
          >
            {loading ? (
              <i className="fa-solid fa-spinner fa-spin" />
            ) : (
              <i className="fa-solid fa-rotate" />
            )}
            Reboot Now
          </button>
          <button
            onClick={onApplyLater}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 glass-control text-stone-700 dark:text-stone-300 text-xs font-bold rounded-lg transition-all border border-stone-300 dark:border-stone-700 hover:border-stone-400 dark:hover:border-stone-600 disabled:opacity-50"
          >
            {loading && <i className="fa-solid fa-spinner fa-spin" />}
            Apply on Next Boot
          </button>
        </div>
      </div>
    </Modal>
  );
};

export default ConfigRebootConfirmModal;
