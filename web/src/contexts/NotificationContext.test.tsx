import React from 'react';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import { NotificationProvider, useNotifications } from './NotificationContext';
import { DEFAULT_USER_PREFERENCES } from '../types/notifications';

vi.mock('./UserContext', () => ({
  useUser: () => ({ user: null }),
}));

function Harness() {
  const { addNotification, notifications, toasts, markAllAsRead } = useNotifications();
  return (
    <div>
      <button onClick={() => addNotification('info', 'Hello', 'World')}>Add</button>
      <button onClick={() => markAllAsRead()}>MarkAll</button>
      <div data-testid="notifications-count">{notifications.length}</div>
      <div data-testid="toasts-count">{toasts.length}</div>
      <div data-testid="unread-count">
        {notifications.filter((n) => !n.read).length}
      </div>
    </div>
  );
}

describe('NotificationProvider', () => {
  it('deduplicates notifications within the window and manages read state', () => {
    vi.useFakeTimers();
    render(
      <NotificationProvider>
        <Harness />
      </NotificationProvider>
    );

    fireEvent.click(screen.getByText('Add'));
    fireEvent.click(screen.getByText('Add'));

    expect(screen.getByTestId('notifications-count').textContent).toBe('1');
    expect(screen.getByTestId('toasts-count').textContent).toBe('1');

    fireEvent.click(screen.getByText('MarkAll'));
    expect(screen.getByTestId('unread-count').textContent).toBe('0');

    act(() => {
      vi.advanceTimersByTime(10000);
    });
    fireEvent.click(screen.getByText('Add'));
    expect(screen.getByTestId('notifications-count').textContent).toBe('2');

    vi.useRealTimers();
  });

  it('updates notification settings via API', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...DEFAULT_USER_PREFERENCES,
        notification_settings: {
          ...DEFAULT_USER_PREFERENCES.notification_settings,
          toasts: {
            ...DEFAULT_USER_PREFERENCES.notification_settings.toasts,
            enabled: false,
          },
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');

    const SettingsHarness = () => {
      const { updateNotificationSettings, preferences } = useNotifications();
      return (
        <div>
          <button
            onClick={() =>
              updateNotificationSettings({
                toasts: { ...preferences!.notification_settings.toasts, enabled: false },
              })
            }
          >
            DisableToasts
          </button>
          <div data-testid="toasts-enabled">
            {preferences?.notification_settings.toasts.enabled ? 'yes' : 'no'}
          </div>
        </div>
      );
    };

    render(
      <NotificationProvider>
        <SettingsHarness />
      </NotificationProvider>
    );

    fireEvent.click(screen.getByText('DisableToasts'));

    await waitFor(() => {
      expect(screen.getByTestId('toasts-enabled').textContent).toBe('no');
    });
  });
});
