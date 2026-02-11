import React, { useState, useRef, useEffect } from 'react';
import { useNotifications } from '../contexts/NotificationContext';
import type { NotificationLevel } from '../types/notifications';

const levelIcons: Record<NotificationLevel, { icon: string; color: string }> = {
  info: { icon: 'fa-circle-info', color: 'text-blue-500' },
  success: { icon: 'fa-circle-check', color: 'text-green-500' },
  warning: { icon: 'fa-triangle-exclamation', color: 'text-amber-500' },
  error: { icon: 'fa-circle-xmark', color: 'text-red-500' },
};

export function NotificationCenter() {
  const {
    notifications,
    unreadCount,
    markAsRead,
    markAllAsRead,
    clearNotifications,
    preferences,
  } = useNotifications();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!preferences?.notification_settings.bell.enabled) return null;

  const formatTime = (date: Date) => {
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return date.toLocaleDateString();
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="relative w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-300 hover:text-sage-600 dark:hover:text-sage-400 rounded-xl transition-all border"
        title="Notifications"
      >
        <i className="fa-solid fa-bell" />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-2 w-80 glass-surface-elevated border border-stone-200 dark:border-black/80 rounded-xl shadow-2xl z-50 overflow-hidden animate-in fade-in zoom-in-95 duration-150">
          {/* Header */}
          <div className="px-4 py-3 border-b border-stone-200 dark:border-black/70 flex items-center justify-between">
            <span className="text-sm font-bold text-stone-900 dark:text-stone-100">
              Notifications
            </span>
            <div className="flex gap-2">
              {unreadCount > 0 && (
                <button
                  onClick={markAllAsRead}
                  className="text-xs text-sage-600 hover:text-sage-700 dark:text-sage-400"
                >
                  Mark all read
                </button>
              )}
              {notifications.length > 0 && (
                <button
                  onClick={clearNotifications}
                  className="text-xs text-stone-500 hover:text-stone-700 dark:text-stone-400"
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          {/* Notification List */}
          <div className="max-h-96 overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="py-8 text-center text-stone-500 dark:text-stone-400">
                <i className="fa-solid fa-bell-slash text-2xl mb-2" />
                <p className="text-sm">No notifications</p>
              </div>
            ) : (
              notifications.map((notif) => {
                const { icon, color } = levelIcons[notif.level];
                return (
                  <div
                    key={notif.id}
                    onClick={() => markAsRead(notif.id)}
                    className={`px-4 py-3 border-b border-stone-100 dark:border-black/60 cursor-pointer hover:bg-stone-50 dark:hover:bg-black/70 transition-colors ${
                      !notif.read ? 'bg-sage-50/50 dark:bg-sage-900/20' : ''
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <i className={`fa-solid ${icon} ${color} mt-0.5`} />
                      <div className="flex-1 min-w-0">
                        <p
                          className={`text-sm ${
                            !notif.read ? 'font-semibold' : ''
                          } text-stone-900 dark:text-stone-100`}
                        >
                          {notif.title}
                        </p>
                        {notif.message && (
                          <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5 line-clamp-2">
                            {notif.message}
                          </p>
                        )}
                        <p className="text-[10px] text-stone-400 dark:text-stone-500 mt-1">
                          {formatTime(notif.timestamp)}
                        </p>
                      </div>
                      {!notif.read && (
                        <div className="w-2 h-2 bg-sage-500 rounded-full mt-1.5" />
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default NotificationCenter;
