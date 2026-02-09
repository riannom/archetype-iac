import React, { createContext, useContext, useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useUser } from './UserContext';
import { API_BASE_URL } from '../api';
import type {
  Notification,
  NotificationLevel,
  UserPreferences,
  NotificationSettings,
  CanvasSettings,
} from '../types/notifications';
import { DEFAULT_USER_PREFERENCES } from '../types/notifications';

// Duration to suppress duplicate notifications (in milliseconds)
const DEDUP_WINDOW_MS = 10000;

interface NotificationContextType {
  // Notifications
  notifications: Notification[];
  unreadCount: number;
  addNotification: (
    level: NotificationLevel,
    title: string,
    message?: string,
    options?: Partial<Notification>
  ) => void;
  markAsRead: (id: string) => void;
  markAllAsRead: () => void;
  clearNotifications: () => void;

  // Toast queue (separate from bell history)
  toasts: Notification[];
  dismissToast: (id: string) => void;

  // Settings
  preferences: UserPreferences | null;
  updateNotificationSettings: (settings: Partial<NotificationSettings>) => Promise<void>;
  updateCanvasSettings: (settings: Partial<CanvasSettings>) => Promise<void>;
  loading: boolean;
}

const NotificationContext = createContext<NotificationContextType | null>(null);

export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const { user } = useUser();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [toasts, setToasts] = useState<Notification[]>([]);
  const [preferences, setPreferences] = useState<UserPreferences | null>(null);
  const [loading, setLoading] = useState(true);

  // Track recent notifications by content hash to prevent duplicates
  const recentNotificationsRef = useRef<Set<string>>(new Set());

  // Fetch preferences from API when user loads
  useEffect(() => {
    if (!user) {
      setPreferences(DEFAULT_USER_PREFERENCES);
      setLoading(false);
      return;
    }

    const fetchPreferences = async () => {
      try {
        const token = localStorage.getItem('token');
        const res = await fetch(`${API_BASE_URL}/auth/preferences`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setPreferences(data);
        } else {
          setPreferences(DEFAULT_USER_PREFERENCES);
        }
      } catch {
        setPreferences(DEFAULT_USER_PREFERENCES);
      } finally {
        setLoading(false);
      }
    };

    fetchPreferences();
  }, [user]);

  const addNotification = useCallback(
    (
      level: NotificationLevel,
      title: string,
      message?: string,
      options?: Partial<Notification>
    ) => {
      // Deduplication: skip if we've shown this exact notification recently
      // Key is level + title + message to allow same title with different messages
      const dedupKey = `${level}:${title}:${message || ''}`;
      if (recentNotificationsRef.current.has(dedupKey)) {
        return; // Skip duplicate notification
      }

      // Mark this notification as shown and auto-clear after window
      recentNotificationsRef.current.add(dedupKey);
      setTimeout(() => {
        recentNotificationsRef.current.delete(dedupKey);
      }, DEDUP_WINDOW_MS);

      const id = `notif-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const notification: Notification = {
        id,
        level,
        title,
        message,
        timestamp: new Date(),
        read: false,
        ...options,
      };

      const category = notification.category;

      // Check if a notification category passes filter settings
      const passesFilter = (settings: {
        showJobStart: boolean;
        showJobComplete: boolean;
        showJobFailed: boolean;
        showJobRetry: boolean;
        showImageSync: boolean;
        showSyncJobs: boolean;
      }) => {
        if (!category) return true;
        const isSync = category.startsWith('sync-');
        if (isSync && !settings.showSyncJobs) return false;
        const baseCategory = isSync ? category.replace('sync-', 'job-') : category;
        switch (baseCategory) {
          case 'job-start': return settings.showJobStart;
          case 'job-complete': return settings.showJobComplete;
          case 'job-failed': return settings.showJobFailed;
          case 'job-retry': return settings.showJobRetry;
          case 'image-sync': return settings.showImageSync;
          default: return true;
        }
      };

      // Add to bell history if enabled and passes filters
      if (preferences?.notification_settings.bell.enabled &&
          passesFilter(preferences.notification_settings.bell)) {
        setNotifications((prev) => {
          const maxHistory = preferences.notification_settings.bell.maxHistory;
          const updated = [notification, ...prev];
          return updated.slice(0, maxHistory);
        });
      }

      // Add to toast queue if enabled and passes filters
      if (preferences?.notification_settings.toasts.enabled &&
          passesFilter(preferences.notification_settings.toasts)) {
        setToasts((prev) => [...prev, notification]);

        // Auto-dismiss after duration
        setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, preferences.notification_settings.toasts.duration);
      }
    },
    [preferences]
  );

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const markAsRead = useCallback((id: string) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
  }, []);

  const markAllAsRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  const clearNotifications = useCallback(() => {
    setNotifications([]);
  }, []);

  const unreadCount = useMemo(
    () => notifications.filter((n) => !n.read).length,
    [notifications]
  );

  const updateNotificationSettings = useCallback(
    async (settings: Partial<NotificationSettings>) => {
      if (!preferences) return;
      const token = localStorage.getItem('token');
      const newSettings = {
        toasts: { ...preferences.notification_settings.toasts, ...settings.toasts },
        bell: { ...preferences.notification_settings.bell, ...settings.bell },
      };

      try {
        const res = await fetch(`${API_BASE_URL}/auth/preferences`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ notification_settings: newSettings }),
        });
        if (res.ok) {
          const data = await res.json();
          setPreferences(data);
        }
      } catch (e) {
        console.error('Failed to update notification settings:', e);
      }
    },
    [preferences]
  );

  const updateCanvasSettings = useCallback(
    async (settings: Partial<CanvasSettings>) => {
      if (!preferences) return;
      const token = localStorage.getItem('token');
      const newSettings = {
        errorIndicator: {
          ...preferences.canvas_settings.errorIndicator,
          ...settings.errorIndicator,
        },
        showAgentIndicators:
          settings.showAgentIndicators ?? preferences.canvas_settings.showAgentIndicators,
        consoleInBottomPanel:
          settings.consoleInBottomPanel ?? preferences.canvas_settings.consoleInBottomPanel,
        sidebarFilters: {
          ...preferences.canvas_settings.sidebarFilters,
          ...settings.sidebarFilters,
        },
        metricsBarExpanded:
          settings.metricsBarExpanded ?? preferences.canvas_settings.metricsBarExpanded,
      };

      // Optimistic update - apply immediately for responsive UI
      setPreferences({
        ...preferences,
        canvas_settings: newSettings,
      });

      try {
        const res = await fetch(`${API_BASE_URL}/auth/preferences`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ canvas_settings: newSettings }),
        });
        if (res.ok) {
          const data = await res.json();
          setPreferences(data);
        }
      } catch (e) {
        console.error('Failed to update canvas settings:', e);
      }
    },
    [preferences]
  );

  const value: NotificationContextType = useMemo(
    () => ({
      notifications,
      unreadCount,
      addNotification,
      markAsRead,
      markAllAsRead,
      clearNotifications,
      toasts,
      dismissToast,
      preferences,
      updateNotificationSettings,
      updateCanvasSettings,
      loading,
    }),
    [
      notifications,
      unreadCount,
      addNotification,
      markAsRead,
      markAllAsRead,
      clearNotifications,
      toasts,
      dismissToast,
      preferences,
      updateNotificationSettings,
      updateCanvasSettings,
      loading,
    ]
  );

  return (
    <NotificationContext.Provider value={value}>{children}</NotificationContext.Provider>
  );
}

export function useNotifications(): NotificationContextType {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error('useNotifications must be used within a NotificationProvider');
  }
  return context;
}
