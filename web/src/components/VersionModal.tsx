import React, { useState } from 'react';
import { UpdateInfo } from '../api';

interface VersionModalProps {
  isOpen: boolean;
  onClose: () => void;
  updateInfo: UpdateInfo;
}

export const VersionModal: React.FC<VersionModalProps> = ({
  isOpen,
  onClose,
  updateInfo,
}) => {
  const [showUpgradeInstructions, setShowUpgradeInstructions] = useState(false);

  if (!isOpen) return null;

  const formatDate = (dateString: string | null | undefined): string => {
    if (!dateString) return '';
    try {
      return new Date(dateString).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      });
    } catch {
      return dateString;
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl border border-stone-200 dark:border-stone-800 w-full max-w-md mx-4 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
          <h2 className="text-lg font-bold text-stone-900 dark:text-white">
            Version Information
          </h2>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center text-stone-400 hover:text-stone-600 dark:hover:text-stone-200 rounded-lg hover:bg-stone-100 dark:hover:bg-stone-800 transition-all"
          >
            <i className="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="px-6 py-5 space-y-5">
          {/* Current Version */}
          <div className="flex items-center justify-between">
            <span className="text-sm text-stone-500 dark:text-stone-400">
              Current Version
            </span>
            <span className="text-sm font-semibold text-stone-900 dark:text-white font-mono">
              v{updateInfo.current_version}
            </span>
          </div>

          {/* Update Status */}
          {updateInfo.update_available ? (
            <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-xl p-4">
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 rounded-full bg-green-100 dark:bg-green-800/50 flex items-center justify-center flex-shrink-0">
                  <i className="fa-solid fa-arrow-up text-green-600 dark:text-green-400 text-sm" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-semibold text-green-800 dark:text-green-200">
                    Update Available
                  </h3>
                  <p className="text-xs text-green-600 dark:text-green-400 mt-0.5">
                    Version {updateInfo.latest_version} is available
                    {updateInfo.published_at && (
                      <> &middot; Released {formatDate(updateInfo.published_at)}</>
                    )}
                  </p>
                </div>
              </div>

              {/* Release Notes */}
              {updateInfo.release_notes && (
                <div className="mt-4 pt-4 border-t border-green-200 dark:border-green-800">
                  <h4 className="text-xs font-semibold text-green-700 dark:text-green-300 mb-2">
                    Release Notes
                  </h4>
                  <div className="text-xs text-green-600 dark:text-green-400 max-h-32 overflow-y-auto prose prose-sm dark:prose-invert prose-green">
                    <pre className="whitespace-pre-wrap font-sans">
                      {updateInfo.release_notes.length > 500
                        ? updateInfo.release_notes.slice(0, 500) + '...'
                        : updateInfo.release_notes}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          ) : updateInfo.error ? (
            <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl p-4">
              <div className="flex items-start gap-3">
                <div className="w-8 h-8 rounded-full bg-amber-100 dark:bg-amber-800/50 flex items-center justify-center flex-shrink-0">
                  <i className="fa-solid fa-exclamation-triangle text-amber-600 dark:text-amber-400 text-sm" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-amber-800 dark:text-amber-200">
                    Update Check Failed
                  </h3>
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">
                    {updateInfo.error}
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <div className="bg-stone-50 dark:bg-stone-800/50 border border-stone-200 dark:border-stone-700 rounded-xl p-4">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-stone-100 dark:bg-stone-700 flex items-center justify-center flex-shrink-0">
                  <i className="fa-solid fa-check text-stone-500 dark:text-stone-400 text-sm" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-200">
                    Up to Date
                  </h3>
                  <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                    You're running the latest version
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Upgrade Instructions (Expandable) */}
          {updateInfo.update_available && (
            <div className="border border-stone-200 dark:border-stone-700 rounded-xl overflow-hidden">
              <button
                onClick={() => setShowUpgradeInstructions(!showUpgradeInstructions)}
                className="w-full px-4 py-3 flex items-center justify-between text-left bg-stone-50 dark:bg-stone-800/50 hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"
              >
                <span className="text-sm font-medium text-stone-700 dark:text-stone-200">
                  Upgrade Instructions
                </span>
                <i
                  className={`fa-solid fa-chevron-down text-xs text-stone-400 transition-transform ${
                    showUpgradeInstructions ? 'rotate-180' : ''
                  }`}
                />
              </button>
              {showUpgradeInstructions && (
                <div className="px-4 py-3 border-t border-stone-200 dark:border-stone-700 bg-white dark:bg-stone-900">
                  <div className="space-y-3">
                    <p className="text-xs text-stone-600 dark:text-stone-400">
                      To upgrade Archetype, run the following command:
                    </p>
                    <code className="block bg-stone-100 dark:bg-stone-800 rounded-lg px-3 py-2 text-xs font-mono text-stone-700 dark:text-stone-300">
                      ./scripts/upgrade.sh
                    </code>
                    <p className="text-xs text-stone-500 dark:text-stone-500">
                      This will create a backup, pull the latest changes, run migrations, and rebuild the containers.
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-stone-200 dark:border-stone-800 flex items-center justify-between">
          {updateInfo.release_url ? (
            <a
              href={updateInfo.release_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-sage-600 dark:text-sage-400 hover:text-sage-700 dark:hover:text-sage-300 flex items-center gap-1.5 transition-colors"
            >
              <i className="fa-brands fa-github" />
              View on GitHub
            </a>
          ) : (
            <a
              href="https://github.com/riannom/archetype-iac/releases"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-sage-600 dark:text-sage-400 hover:text-sage-700 dark:hover:text-sage-300 flex items-center gap-1.5 transition-colors"
            >
              <i className="fa-brands fa-github" />
              View Releases
            </a>
          )}
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-semibold text-stone-600 dark:text-stone-300 hover:text-stone-900 dark:hover:text-white bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 rounded-lg transition-all"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default VersionModal;
