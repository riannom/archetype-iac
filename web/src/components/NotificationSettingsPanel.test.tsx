import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import { NotificationSettingsPanel } from './NotificationSettingsPanel';
import { DEFAULT_USER_PREFERENCES } from '../types/notifications';

let mockValue: any;

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => mockValue,
}));

describe('NotificationSettingsPanel', () => {
  it('invokes update handlers when toggling settings', () => {
    const updateNotificationSettings = vi.fn();
    const updateCanvasSettings = vi.fn();

    mockValue = {
      preferences: DEFAULT_USER_PREFERENCES,
      updateNotificationSettings,
      updateCanvasSettings,
    };

    render(<NotificationSettingsPanel isOpen onClose={() => {}} />);

    const checkbox = screen.getByLabelText('Enable toast notifications');
    fireEvent.click(checkbox);

    expect(updateNotificationSettings).toHaveBeenCalled();
  });
});
