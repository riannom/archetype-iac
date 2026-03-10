import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NotificationSettingsPanel } from './NotificationSettingsPanel';
import {
  DEFAULT_USER_PREFERENCES,
  DEFAULT_TOAST_SETTINGS,
  DEFAULT_BELL_SETTINGS,
  DEFAULT_CANVAS_ERROR_SETTINGS,
  type UserPreferences,
} from '../types/notifications';

let mockValue: any;

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => mockValue,
}));

function makePreferences(overrides?: Partial<UserPreferences>): UserPreferences {
  return {
    ...DEFAULT_USER_PREFERENCES,
    ...overrides,
  };
}

function setup(
  opts: {
    preferences?: UserPreferences;
    isOpen?: boolean;
    onClose?: () => void;
  } = {},
) {
  const updateNotificationSettings = vi.fn();
  const updateCanvasSettings = vi.fn();
  const onClose = opts.onClose ?? vi.fn();

  mockValue = {
    preferences: opts.preferences ?? makePreferences(),
    updateNotificationSettings,
    updateCanvasSettings,
  };

  const result = render(
    <NotificationSettingsPanel isOpen={opts.isOpen ?? true} onClose={onClose} />,
  );

  return { updateNotificationSettings, updateCanvasSettings, onClose, ...result };
}

describe('NotificationSettingsPanel', () => {
  beforeEach(() => {
    mockValue = undefined;
  });

  // --- Toggle interactions (original test) ---

  it('invokes update handlers when toggling settings', () => {
    const { updateNotificationSettings } = setup();

    const checkbox = screen.getByLabelText('Enable toast notifications');
    fireEvent.click(checkbox);

    expect(updateNotificationSettings).toHaveBeenCalled();
  });

  // --- Rendering sections ---

  it('renders all four section headings when open', () => {
    setup();
    expect(screen.getByText('Toast Notifications')).toBeInTheDocument();
    expect(screen.getByText('Notification Center')).toBeInTheDocument();
    expect(screen.getByText('Canvas Error Indicators')).toBeInTheDocument();
    expect(screen.getByText('Canvas Display')).toBeInTheDocument();
  });

  it('renders the panel header with title', () => {
    setup();
    expect(screen.getByText('Notification Settings')).toBeInTheDocument();
  });

  it('renders the Done button in the footer', () => {
    setup();
    expect(screen.getByText('Done')).toBeInTheDocument();
  });

  // --- isOpen / null guard ---

  it('renders nothing when isOpen is false', () => {
    const { container } = setup({ isOpen: false });
    expect(container.innerHTML).toBe('');
  });

  it('renders nothing when preferences is null', () => {
    mockValue = {
      preferences: null,
      updateNotificationSettings: vi.fn(),
      updateCanvasSettings: vi.fn(),
    };
    const { container } = render(
      <NotificationSettingsPanel isOpen onClose={() => {}} />,
    );
    expect(container.innerHTML).toBe('');
  });

  // --- Close button ---

  it('calls onClose when the X button is clicked', () => {
    const onClose = vi.fn();
    setup({ onClose });
    // The X button is the first button (header close)
    const closeBtn = screen.getByRole('button', { name: '' });
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when Done button is clicked', () => {
    const onClose = vi.fn();
    setup({ onClose });
    fireEvent.click(screen.getByText('Done'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // --- Toast child toggles hidden when parent disabled ---

  it('hides toast child options when toasts.enabled is false', () => {
    const prefs = makePreferences({
      notification_settings: {
        toasts: { ...DEFAULT_TOAST_SETTINGS, enabled: false },
        bell: { ...DEFAULT_BELL_SETTINGS, enabled: false },
      },
    });
    setup({ preferences: prefs });
    // With both parents disabled, no child options should appear
    expect(screen.queryByText('Job started')).not.toBeInTheDocument();
    expect(screen.queryByText('Job completed')).not.toBeInTheDocument();
    expect(screen.queryByText('Job failed')).not.toBeInTheDocument();
    expect(screen.queryByText('Image sync events')).not.toBeInTheDocument();
  });

  it('shows toast child options when toasts.enabled is true', () => {
    setup(); // defaults have toasts enabled
    // There are two "Job started" labels (toast + bell), check at least one exists
    expect(screen.getAllByText('Job started').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Job completed').length).toBeGreaterThanOrEqual(1);
  });

  // --- Bell child toggles hidden when parent disabled ---

  it('hides bell child options when bell.enabled is false', () => {
    const prefs = makePreferences({
      notification_settings: {
        toasts: DEFAULT_TOAST_SETTINGS,
        bell: { ...DEFAULT_BELL_SETTINGS, enabled: false },
      },
    });
    setup({ preferences: prefs });
    // Toast children still present, but bell children should not add duplicates
    // With bell disabled and toast enabled, "Job started" appears once (toast only)
    const jobStartLabels = screen.getAllByText('Job started');
    expect(jobStartLabels).toHaveLength(1);
  });

  // --- Toggle interactions call correct handlers ---

  it('calls updateNotificationSettings when toggling bell enabled', () => {
    const { updateNotificationSettings } = setup();
    const bellCheckbox = screen.getByLabelText('Enable notification center (bell icon)');
    fireEvent.click(bellCheckbox);
    expect(updateNotificationSettings).toHaveBeenCalledWith({
      bell: expect.objectContaining({ enabled: false }),
    });
  });

  it('calls updateCanvasSettings when toggling error icon', () => {
    const { updateCanvasSettings } = setup();
    const errorIconCheckbox = screen.getByLabelText('Show error icon on nodes');
    fireEvent.click(errorIconCheckbox);
    expect(updateCanvasSettings).toHaveBeenCalledWith({
      errorIndicator: expect.objectContaining({ showIcon: false }),
    });
  });

  it('calls updateCanvasSettings when toggling agent indicators', () => {
    const { updateCanvasSettings } = setup();
    const agentCheckbox = screen.getByLabelText('Show agent indicators on nodes');
    fireEvent.click(agentCheckbox);
    expect(updateCanvasSettings).toHaveBeenCalledWith({
      showAgentIndicators: false,
    });
  });

  it('calls updateCanvasSettings when toggling console in bottom panel', () => {
    const { updateCanvasSettings } = setup();
    const consoleCheckbox = screen.getByLabelText('Open console in bottom panel');
    fireEvent.click(consoleCheckbox);
    expect(updateCanvasSettings).toHaveBeenCalledWith({
      consoleInBottomPanel: true,
    });
  });

  // --- Current preferences reflected ---

  it('reflects current toast position in the select', () => {
    const prefs = makePreferences({
      notification_settings: {
        toasts: { ...DEFAULT_TOAST_SETTINGS, position: 'top-left' },
        bell: DEFAULT_BELL_SETTINGS,
      },
    });
    setup({ preferences: prefs });
    const select = screen.getByDisplayValue('Top Left');
    expect(select).toBeInTheDocument();
  });

  it('reflects current toast duration in the number input', () => {
    const prefs = makePreferences({
      notification_settings: {
        toasts: { ...DEFAULT_TOAST_SETTINGS, duration: 8000 },
        bell: DEFAULT_BELL_SETTINGS,
      },
    });
    setup({ preferences: prefs });
    const durationInput = screen.getByDisplayValue('8');
    expect(durationInput).toBeInTheDocument();
  });
});
