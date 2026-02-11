import React from 'react';
import { useNotifications } from '../contexts/NotificationContext';

interface NotificationSettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function NotificationSettingsPanel({ isOpen, onClose }: NotificationSettingsPanelProps) {
  const { preferences, updateNotificationSettings, updateCanvasSettings } = useNotifications();

  if (!isOpen || !preferences) return null;

  const { toasts, bell } = preferences.notification_settings;
  const { errorIndicator, showAgentIndicators, consoleInBottomPanel } = preferences.canvas_settings;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="glass-surface-elevated border border-stone-200 dark:border-stone-700 rounded-2xl w-[500px] max-h-[85vh] flex flex-col overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="p-5 border-b border-stone-100 dark:border-stone-800 flex justify-between items-center">
          <h3 className="text-stone-900 dark:text-stone-100 font-bold text-sm uppercase tracking-wider">
            Notification Settings
          </h3>
          <button
            onClick={onClose}
            className="text-stone-500 hover:text-stone-900 dark:hover:text-white transition-colors"
          >
            <i className="fa-solid fa-times"></i>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 p-6 overflow-y-auto space-y-6">
          {/* Toast Settings */}
          <div>
            <h4 className="text-xs font-black text-stone-500 dark:text-stone-400 uppercase tracking-widest mb-3">
              Toast Notifications
            </h4>
            <div className="space-y-3">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={toasts.enabled}
                  onChange={(e) =>
                    updateNotificationSettings({ toasts: { ...toasts, enabled: e.target.checked } })
                  }
                  className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                />
                <span className="text-sm text-stone-700 dark:text-stone-300">
                  Enable toast notifications
                </span>
              </label>

              {toasts.enabled && (
                <div className="ml-7 space-y-3">
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={toasts.showJobStart}
                      onChange={(e) =>
                        updateNotificationSettings({
                          toasts: { ...toasts, showJobStart: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">Job started</span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={toasts.showJobComplete}
                      onChange={(e) =>
                        updateNotificationSettings({
                          toasts: { ...toasts, showJobComplete: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">
                      Job completed
                    </span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={toasts.showJobFailed}
                      onChange={(e) =>
                        updateNotificationSettings({
                          toasts: { ...toasts, showJobFailed: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">Job failed</span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={toasts.showImageSync}
                      onChange={(e) =>
                        updateNotificationSettings({
                          toasts: { ...toasts, showImageSync: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">
                      Image sync events
                    </span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={toasts.showSyncJobs ?? false}
                      onChange={(e) =>
                        updateNotificationSettings({
                          toasts: { ...toasts, showSyncJobs: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">
                      State sync jobs
                    </span>
                  </label>

                  <div className="pt-2">
                    <label className="text-xs font-medium text-stone-500 dark:text-stone-400">
                      Position
                    </label>
                    <select
                      value={toasts.position}
                      onChange={(e) =>
                        updateNotificationSettings({
                          toasts: { ...toasts, position: e.target.value as any },
                        })
                      }
                      className="mt-1 block w-full rounded-lg border-stone-300 dark:border-stone-600 dark:bg-stone-800 text-sm text-stone-700 dark:text-stone-300 focus:border-sage-500 focus:ring-sage-500"
                    >
                      <option value="bottom-right">Bottom Right</option>
                      <option value="bottom-left">Bottom Left</option>
                      <option value="top-right">Top Right</option>
                      <option value="top-left">Top Left</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-xs font-medium text-stone-500 dark:text-stone-400">
                      Duration (seconds)
                    </label>
                    <input
                      type="number"
                      min="1"
                      max="30"
                      value={toasts.duration / 1000}
                      onChange={(e) =>
                        updateNotificationSettings({
                          toasts: { ...toasts, duration: parseInt(e.target.value) * 1000 },
                        })
                      }
                      className="mt-1 block w-full rounded-lg border-stone-300 dark:border-stone-600 dark:bg-stone-800 text-sm text-stone-700 dark:text-stone-300 focus:border-sage-500 focus:ring-sage-500"
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Bell Settings */}
          <div>
            <h4 className="text-xs font-black text-stone-500 dark:text-stone-400 uppercase tracking-widest mb-3">
              Notification Center
            </h4>
            <div className="space-y-3">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={bell.enabled}
                  onChange={(e) =>
                    updateNotificationSettings({ bell: { ...bell, enabled: e.target.checked } })
                  }
                  className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                />
                <span className="text-sm text-stone-700 dark:text-stone-300">
                  Enable notification center (bell icon)
                </span>
              </label>

              {bell.enabled && (
                <div className="ml-7 space-y-3">
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={bell.showJobStart ?? false}
                      onChange={(e) =>
                        updateNotificationSettings({
                          bell: { ...bell, showJobStart: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">Job started</span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={bell.showJobComplete ?? false}
                      onChange={(e) =>
                        updateNotificationSettings({
                          bell: { ...bell, showJobComplete: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">
                      Job completed
                    </span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={bell.showJobFailed ?? true}
                      onChange={(e) =>
                        updateNotificationSettings({
                          bell: { ...bell, showJobFailed: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">Job failed</span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={bell.showImageSync ?? false}
                      onChange={(e) =>
                        updateNotificationSettings({
                          bell: { ...bell, showImageSync: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">
                      Image sync events
                    </span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={bell.showSyncJobs ?? false}
                      onChange={(e) =>
                        updateNotificationSettings({
                          bell: { ...bell, showSyncJobs: e.target.checked },
                        })
                      }
                      className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                    />
                    <span className="text-sm text-stone-600 dark:text-stone-400">
                      State sync jobs
                    </span>
                  </label>
                </div>
              )}
            </div>
          </div>

          {/* Canvas Error Indicator Settings */}
          <div>
            <h4 className="text-xs font-black text-stone-500 dark:text-stone-400 uppercase tracking-widest mb-3">
              Canvas Error Indicators
            </h4>
            <div className="space-y-3">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={errorIndicator.showIcon}
                  onChange={(e) =>
                    updateCanvasSettings({
                      errorIndicator: { ...errorIndicator, showIcon: e.target.checked },
                    })
                  }
                  className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                />
                <span className="text-sm text-stone-700 dark:text-stone-300">
                  Show error icon on nodes
                </span>
              </label>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={errorIndicator.showBorder}
                  onChange={(e) =>
                    updateCanvasSettings({
                      errorIndicator: { ...errorIndicator, showBorder: e.target.checked },
                    })
                  }
                  className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                />
                <span className="text-sm text-stone-700 dark:text-stone-300">
                  Show red border on error nodes
                </span>
              </label>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={errorIndicator.pulseAnimation}
                  onChange={(e) =>
                    updateCanvasSettings({
                      errorIndicator: { ...errorIndicator, pulseAnimation: e.target.checked },
                    })
                  }
                  className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                />
                <span className="text-sm text-stone-700 dark:text-stone-300">
                  Pulse animation on error
                </span>
              </label>
            </div>
          </div>

          {/* Agent Indicators */}
          <div>
            <h4 className="text-xs font-black text-stone-500 dark:text-stone-400 uppercase tracking-widest mb-3">
              Canvas Display
            </h4>
            <div className="space-y-3">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showAgentIndicators}
                  onChange={(e) =>
                    updateCanvasSettings({ showAgentIndicators: e.target.checked })
                  }
                  className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                />
                <span className="text-sm text-stone-700 dark:text-stone-300">
                  Show agent indicators on nodes
                </span>
              </label>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={consoleInBottomPanel ?? false}
                  onChange={(e) =>
                    updateCanvasSettings({ consoleInBottomPanel: e.target.checked })
                  }
                  className="w-4 h-4 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                />
                <span className="text-sm text-stone-700 dark:text-stone-300">
                  Open console in bottom panel
                </span>
              </label>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="p-5 border-t border-stone-100 dark:border-stone-800 flex justify-end">
          <button
            onClick={onClose}
            className="px-6 py-2 bg-sage-600 hover:bg-sage-500 text-white font-bold rounded-lg transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

export default NotificationSettingsPanel;
