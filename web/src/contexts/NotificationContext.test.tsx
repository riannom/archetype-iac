import React from 'react';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, vi } from 'vitest';
import { NotificationProvider, useNotifications } from './NotificationContext';
import { DEFAULT_USER_PREFERENCES } from '../types/notifications';

const mockUseUser = vi.fn(() => ({ user: null }));
const mockUser = { id: 'user-1' };

vi.mock('./UserContext', () => ({
  useUser: () => mockUseUser(),
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
  beforeEach(() => {
    mockUseUser.mockReturnValue({ user: null });
    localStorage.clear();
    vi.unstubAllGlobals();
  });

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

  it('skips no-op canvas preference writes', async () => {
    mockUseUser.mockReturnValue({ user: mockUser });
    localStorage.setItem('token', 'test-token');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...DEFAULT_USER_PREFERENCES,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const CanvasHarness = () => {
      const { updateCanvasSettings, preferences } = useNotifications();
      if (!preferences) {
        return <div data-testid="canvas-loading">loading</div>;
      }
      return (
        <div>
          <div data-testid="metrics-expanded">
            {preferences.canvas_settings.metricsBarExpanded ? 'yes' : 'no'}
          </div>
          <button
            onClick={() => updateCanvasSettings({ metricsBarExpanded: false })}
          >
            SaveCanvas
          </button>
        </div>
      );
    };

    render(
      <NotificationProvider>
        <CanvasHarness />
      </NotificationProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('metrics-expanded').textContent).toBe('no');
    });
    const patchCallsBefore = fetchMock.mock.calls.filter(
      ([, options]) => (options as RequestInit | undefined)?.method === 'PATCH'
    ).length;

    fireEvent.click(screen.getByText('SaveCanvas'));

    await act(async () => {
      await Promise.resolve();
    });

    const patchCallsAfter = fetchMock.mock.calls.filter(
      ([, options]) => (options as RequestInit | undefined)?.method === 'PATCH'
    ).length;
    expect(patchCallsAfter).toBe(patchCallsBefore);
  });
});
