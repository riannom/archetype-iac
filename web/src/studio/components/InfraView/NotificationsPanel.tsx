import React, { useEffect, useState, useCallback } from 'react';
import { getLabInfraNotifications, InfraNotification } from '../../../api';

interface NotificationsPanelProps {
  labId: string;
  /** Increment to trigger a re-fetch (e.g. on WS link_state change) */
  refreshKey: number;
}

const SEVERITY_ICONS: Record<string, string> = {
  error: 'fa-circle-exclamation',
  warning: 'fa-triangle-exclamation',
  info: 'fa-circle-info',
};

const SEVERITY_COLORS: Record<string, { dot: string; text: string; bg: string }> = {
  error: { dot: 'bg-red-500', text: 'text-red-400', bg: 'bg-red-950/20' },
  warning: { dot: 'bg-amber-500', text: 'text-amber-400', bg: 'bg-amber-950/15' },
  info: { dot: 'bg-blue-500', text: 'text-blue-400', bg: 'bg-blue-950/15' },
};

const CATEGORY_LABELS: Record<string, string> = {
  tunnel_cleanup: 'Tunnel Cleanup',
  tunnel_failed: 'Tunnel Failed',
  link_error: 'Link Error',
  node_error: 'Node Error',
};

function relativeTime(timestamp: string | null): string {
  if (!timestamp) return '';
  const diff = Date.now() - new Date(timestamp).getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

const NotificationsPanel: React.FC<NotificationsPanelProps> = ({ labId, refreshKey }) => {
  const [notifications, setNotifications] = useState<InfraNotification[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchNotifications = useCallback(async () => {
    try {
      const data = await getLabInfraNotifications(labId);
      setNotifications(data.notifications);
    } catch {
      // Silently fail - panel will show empty state
    } finally {
      setLoading(false);
    }
  }, [labId]);

  useEffect(() => {
    fetchNotifications();
  }, [fetchNotifications, refreshKey]);

  // Poll every 30s for background updates
  useEffect(() => {
    const timer = setInterval(fetchNotifications, 30_000);
    return () => clearInterval(timer);
  }, [fetchNotifications]);

  if (loading) {
    return (
      <div className="flex-1 min-h-0 overflow-auto px-4 py-4">
        <div className="text-xs text-stone-600 italic">Loading notifications...</div>
      </div>
    );
  }

  if (notifications.length === 0) {
    return (
      <div className="flex-1 min-h-0 overflow-auto px-4 py-4">
        <div className="text-xs text-stone-600 italic flex items-center gap-2">
          <i className="fa-solid fa-check-circle text-green-600" />
          No infrastructure issues detected
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-auto px-4 py-2">
      <div className="space-y-1.5">
        {notifications.map((n) => {
          const colors = SEVERITY_COLORS[n.severity] || SEVERITY_COLORS.info;
          const icon = SEVERITY_ICONS[n.severity] || SEVERITY_ICONS.info;
          const categoryLabel = CATEGORY_LABELS[n.category] || n.category;

          return (
            <div
              key={n.id}
              className={`rounded-md border border-stone-800/50 px-3 py-2 ${colors.bg}`}
            >
              <div className="flex items-start gap-2">
                <i className={`fa-solid ${icon} ${colors.text} text-xs mt-0.5 flex-shrink-0`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-medium ${colors.text}`}>
                      {n.title}
                    </span>
                    <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-stone-800/50 text-stone-500 font-mono uppercase">
                      {categoryLabel}
                    </span>
                    {n.timestamp && (
                      <span className="text-[10px] text-stone-600 ml-auto flex-shrink-0">
                        {relativeTime(n.timestamp)}
                      </span>
                    )}
                  </div>
                  {n.detail && (
                    <div className="text-[11px] text-stone-500 mt-0.5 font-mono break-all">
                      {n.detail}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default NotificationsPanel;
