import React, { useState } from 'react';

interface ConfigActionsProps {
  extracting: boolean;
  orphanedCount: number;
  onExtract: () => void;
  onDownloadAll: () => void;
  onDeleteAllOrphaned: () => void;
}

export const ConfigActions: React.FC<ConfigActionsProps> = ({
  extracting,
  orphanedCount,
  onExtract,
  onDownloadAll,
  onDeleteAllOrphaned,
}) => {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const handleDeleteOrphaned = () => {
    setShowDeleteConfirm(true);
  };

  const confirmDelete = () => {
    onDeleteAllOrphaned();
    setShowDeleteConfirm(false);
  };

  return (
    <>
      <div className="flex items-center gap-2">
        {/* Extract Configs */}
        <button
          onClick={onExtract}
          disabled={extracting}
          className="px-3 py-2 text-xs font-bold text-white bg-sage-600 hover:bg-sage-700 disabled:bg-sage-400 rounded-lg transition-colors flex items-center gap-2"
        >
          {extracting ? (
            <>
              <i className="fas fa-spinner fa-spin" />
              Extracting...
            </>
          ) : (
            <>
              <i className="fas fa-download" />
              Extract Configs
            </>
          )}
        </button>

        {/* Download All */}
        <button
          onClick={onDownloadAll}
          className="px-3 py-2 text-xs font-bold text-stone-700 dark:text-stone-300 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 rounded-lg transition-colors flex items-center gap-2"
        >
          <i className="fas fa-file-zipper" />
          Download All
        </button>

        {/* Delete Orphaned (only shows when orphanedCount > 0) */}
        {orphanedCount > 0 && (
          <button
            onClick={handleDeleteOrphaned}
            className="px-3 py-2 text-xs font-bold text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 hover:bg-red-100 dark:hover:bg-red-900 rounded-lg transition-colors flex items-center gap-2"
          >
            <i className="fas fa-trash-can" />
            Delete Orphaned
            <span className="px-1.5 py-0.5 text-xs font-bold text-white bg-red-600 rounded">
              {orphanedCount}
            </span>
          </button>
        )}

      </div>

      {/* Delete Confirmation Dialog */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-stone-900 rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <h3 className="text-lg font-bold text-stone-900 dark:text-stone-100 mb-4">
              Delete Orphaned Configs
            </h3>
            <p className="text-stone-600 dark:text-stone-400 mb-6">
              Delete all {orphanedCount} orphaned config snapshot{orphanedCount !== 1 ? 's' : ''}? This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 text-sm font-medium text-stone-700 dark:text-stone-300 bg-stone-200 dark:bg-stone-800 hover:bg-stone-300 dark:hover:bg-stone-700 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors"
              >
                Delete All
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
