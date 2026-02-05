import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import { NotificationCenter } from './NotificationCenter';

let mockValue: any;

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => mockValue,
}));

describe('NotificationCenter', () => {
  it('renders unread badge and actions', () => {
    const markAllAsRead = vi.fn();
    const clearNotifications = vi.fn();
    mockValue = {
      notifications: [
        {
          id: 'n1',
          level: 'info',
          title: 'Hello',
          timestamp: new Date(),
          read: false,
        },
      ],
      unreadCount: 1,
      markAsRead: vi.fn(),
      markAllAsRead,
      clearNotifications,
      preferences: {
        notification_settings: { bell: { enabled: true }, toasts: { enabled: true } },
      },
    };

    render(<NotificationCenter />);

    const button = screen.getByTitle('Notifications');
    fireEvent.click(button);

    expect(screen.getByText('1')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Mark all read'));
    fireEvent.click(screen.getByText('Clear'));

    expect(markAllAsRead).toHaveBeenCalledTimes(1);
    expect(clearNotifications).toHaveBeenCalledTimes(1);
  });

  it('returns null when bell is disabled', () => {
    mockValue = {
      notifications: [],
      unreadCount: 0,
      markAsRead: vi.fn(),
      markAllAsRead: vi.fn(),
      clearNotifications: vi.fn(),
      preferences: {
        notification_settings: { bell: { enabled: false }, toasts: { enabled: true } },
      },
    };

    const { container } = render(<NotificationCenter />);
    expect(container.firstChild).toBeNull();
  });
});
