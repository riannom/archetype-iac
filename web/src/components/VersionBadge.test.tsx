import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import VersionBadge from './VersionBadge';

const checkForUpdates = vi.fn();

declare global {
  var __APP_VERSION__: string | undefined;
}

vi.mock('../api', () => ({
  checkForUpdates: () => checkForUpdates(),
}));

describe('VersionBadge', () => {
  beforeEach(() => {
    checkForUpdates.mockReset();
    globalThis.__APP_VERSION__ = undefined;
  });

  it('shows update indicator when update is available', async () => {
    checkForUpdates.mockResolvedValue({
      current_version: '1.0.0',
      latest_version: '1.1.0',
      update_available: true,
    });

    render(<VersionBadge />);

    expect(await screen.findByText('v1.0.0')).toBeInTheDocument();
    expect(screen.getByTitle('Update available: v1.1.0')).toBeInTheDocument();
  });

  it('falls back to build version when update check fails', async () => {
    globalThis.__APP_VERSION__ = '2.0.0';
    checkForUpdates.mockRejectedValue(new Error('boom'));

    render(<VersionBadge />);

    await waitFor(() => {
      expect(screen.getByText('v2.0.0')).toBeInTheDocument();
    });
  });

  it('opens the version modal on click', async () => {
    checkForUpdates.mockResolvedValue({
      current_version: '1.0.0',
      update_available: false,
    });

    render(<VersionBadge />);

    const button = await screen.findByText('v1.0.0');
    fireEvent.click(button);

    expect(await screen.findByText('Version Information')).toBeInTheDocument();
  });
});
