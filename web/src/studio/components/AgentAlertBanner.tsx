import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiRequest } from '../../api';

interface AgentAlert {
  agent_id: string;
  agent_name: string;
  error_message: string;
  error_since: string;
}

interface SystemAlertsResponse {
  alerts: AgentAlert[];
  agent_error_count: number;
}

/**
 * Formats a duration from a timestamp to a human-readable string.
 * e.g., "5m", "2h 15m", "1d 3h"
 */
const formatDuration = (isoTimestamp: string): string => {
  if (!isoTimestamp) return '';

  const start = new Date(isoTimestamp);
  const now = new Date();
  const diffMs = now.getTime() - start.getTime();

  const minutes = Math.floor(diffMs / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (days > 0) {
    const remainingHours = hours % 24;
    return remainingHours > 0 ? `${days}d ${remainingHours}h` : `${days}d`;
  }
  if (hours > 0) {
    const remainingMinutes = minutes % 60;
    return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
  }
  return `${Math.max(1, minutes)}m`;
};

interface AgentAlertBannerProps {
  className?: string;
}

const AgentAlertBanner: React.FC<AgentAlertBannerProps> = ({ className = '' }) => {
  const navigate = useNavigate();
  const [alerts, setAlerts] = useState<AgentAlert[]>([]);
  const [dismissed, setDismissed] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchAlerts = useCallback(async () => {
    try {
      const data = await apiRequest<SystemAlertsResponse>('/system/alerts');
      setAlerts(data.alerts || []);
      // If new alerts appear after dismissal, show banner again
      if (data.alerts.length > 0 && dismissed) {
        // Only reset dismissed if we have different alerts
        const currentIds = new Set(alerts.map(a => a.agent_id));
        const newIds = new Set(data.alerts.map(a => a.agent_id));
        const hasNewAlerts = data.alerts.some(a => !currentIds.has(a.agent_id));
        if (hasNewAlerts) {
          setDismissed(false);
        }
      }
    } catch (err) {
      // Silently fail - alerts endpoint may not exist on older API versions
      console.debug('Failed to fetch system alerts:', err);
    } finally {
      setLoading(false);
    }
  }, [alerts, dismissed]);

  useEffect(() => {
    fetchAlerts();
    // Poll every 30 seconds
    const interval = setInterval(fetchAlerts, 30000);
    return () => clearInterval(interval);
  }, [fetchAlerts]);

  // Don't render if no alerts, dismissed, or still loading
  if (loading || dismissed || alerts.length === 0) {
    return null;
  }

  const handleDismiss = () => {
    setDismissed(true);
  };

  const handleNavigateToHosts = () => {
    navigate('/hosts');
  };

  return (
    <div className={`bg-red-600 dark:bg-red-700 text-white px-4 py-2 ${className}`}>
      <div className="flex items-center justify-between max-w-screen-xl mx-auto">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <i className="fa-solid fa-triangle-exclamation text-red-200 flex-shrink-0"></i>
          <div className="flex-1 min-w-0">
            <span className="font-semibold text-sm">
              {alerts.length === 1 ? 'Agent Error' : `${alerts.length} Agent Errors`}
            </span>
            {alerts.length === 1 ? (
              <span className="text-sm text-red-100 ml-2 truncate">
                <span className="font-medium">{alerts[0].agent_name}:</span>{' '}
                {alerts[0].error_message}
                {alerts[0].error_since && (
                  <span className="text-red-200 ml-1">
                    ({formatDuration(alerts[0].error_since)})
                  </span>
                )}
              </span>
            ) : (
              <span className="text-sm text-red-100 ml-2">
                {alerts.map(a => a.agent_name).join(', ')}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0 ml-4">
          <button
            onClick={handleNavigateToHosts}
            className="px-3 py-1 bg-red-500 hover:bg-red-400 dark:bg-red-800 dark:hover:bg-red-600 text-white text-xs font-medium rounded transition-colors"
          >
            View Details
          </button>
          <button
            onClick={handleDismiss}
            className="p-1 hover:bg-red-500 dark:hover:bg-red-600 rounded transition-colors"
            title="Dismiss (will reappear on page refresh)"
          >
            <i className="fa-solid fa-times text-red-200 hover:text-white"></i>
          </button>
        </div>
      </div>
    </div>
  );
};

export default AgentAlertBanner;
