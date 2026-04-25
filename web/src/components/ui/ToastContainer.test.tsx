import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ToastContainer } from './ToastContainer';
import { DEFAULT_USER_PREFERENCES } from '../../types/notifications';

let mockValue: any;

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockValue,
}));

const baseToast = {
  id: 'toast-1',
  level: 'info' as const,
  title: 'Hello',
  message: 'World',
  timestamp: new Date(),
  read: false,
};

const prefsWithPosition = (position: string, enabled = true) => ({
  ...DEFAULT_USER_PREFERENCES,
  notification_settings: {
    ...DEFAULT_USER_PREFERENCES.notification_settings,
    toasts: {
      ...DEFAULT_USER_PREFERENCES.notification_settings.toasts,
      enabled,
      position: position as 'bottom-right',
    },
  },
});

describe('ToastContainer', () => {
  it('renders toasts and dismisses them', () => {
    const dismissToast = vi.fn();
    mockValue = {
      toasts: [baseToast],
      dismissToast,
      preferences: DEFAULT_USER_PREFERENCES,
    };

    render(<ToastContainer />);

    expect(screen.getByText('Hello')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button'));
    expect(dismissToast).toHaveBeenCalledWith('toast-1');
  });

  it('renders nothing when toasts are disabled in preferences', () => {
    mockValue = {
      toasts: [baseToast],
      dismissToast: vi.fn(),
      preferences: prefsWithPosition('bottom-right', false),
    };

    const { container } = render(<ToastContainer />);
    expect(container.ownerDocument.querySelector('div.fixed')).toBeNull();
    expect(screen.queryByText('Hello')).toBeNull();
  });

  it('renders nothing when preferences are absent (auth not yet loaded)', () => {
    mockValue = {
      toasts: [baseToast],
      dismissToast: vi.fn(),
      preferences: null,
    };

    const { container } = render(<ToastContainer />);
    expect(container.ownerDocument.querySelector('div.fixed')).toBeNull();
  });

  it('applies the configured position class', () => {
    mockValue = {
      toasts: [baseToast],
      dismissToast: vi.fn(),
      preferences: prefsWithPosition('top-left'),
    };

    const { container } = render(<ToastContainer />);
    const portalRoot = container.ownerDocument.querySelector('div.fixed');
    expect(portalRoot?.className).toMatch(/top-4 left-4/);
  });

  it('falls back to bottom-right when the configured position is unknown', () => {
    mockValue = {
      toasts: [baseToast],
      dismissToast: vi.fn(),
      preferences: prefsWithPosition('north-pole'),
    };

    const { container } = render(<ToastContainer />);
    const portalRoot = container.ownerDocument.querySelector('div.fixed');
    expect(portalRoot?.className).toMatch(/bottom-4 right-4/);
  });
});
