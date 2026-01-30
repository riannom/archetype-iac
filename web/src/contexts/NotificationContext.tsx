import React, { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react';
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

      // Add to bell history if enabled
      if (preferences?.notification_settings.bell.enabled) {
        setNotifications((prev) => {
          const maxHistory = preferences.notification_settings.bell.maxHistory;
          const updated = [notification, ...prev];
          return updated.slice(0, maxHistory);
        });
      }

      // Add to toast queue if enabled
      if (preferences?.notification_settings.toasts.enabled) {
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
      };

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
