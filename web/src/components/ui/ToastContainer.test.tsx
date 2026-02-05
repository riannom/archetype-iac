import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import { ToastContainer } from './ToastContainer';
import { DEFAULT_USER_PREFERENCES } from '../../types/notifications';

let mockValue: any;

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockValue,
}));

describe('ToastContainer', () => {
  it('renders toasts and dismisses them', () => {
    const dismissToast = vi.fn();
    mockValue = {
      toasts: [
        {
          id: 'toast-1',
          level: 'info',
          title: 'Hello',
          message: 'World',
          timestamp: new Date(),
          read: false,
        },
      ],
      dismissToast,
      preferences: DEFAULT_USER_PREFERENCES,
    };

    render(<ToastContainer />);

    expect(screen.getByText('Hello')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button'));
    expect(dismissToast).toHaveBeenCalledWith('toast-1');
  });
});
