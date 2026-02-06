import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { VersionModal } from './VersionModal';

const baseUpdateInfo = {
  current_version: '1.0.0',
  update_available: false,
};

describe('VersionModal', () => {
  it('renders update available state and toggles instructions', () => {
    render(
      <VersionModal
        isOpen={true}
        onClose={() => undefined}
        updateInfo={{
          ...baseUpdateInfo,
          update_available: true,
          latest_version: '1.1.0',
          published_at: '2026-02-01T00:00:00Z',
          release_notes: 'Line 1\nLine 2',
        }}
      />
    );

    expect(screen.getByText('Update Available')).toBeInTheDocument();
    expect(screen.getByText(/Version 1\.1\.0 is available/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Upgrade Instructions'));
    expect(screen.getByText('./scripts/upgrade.sh')).toBeInTheDocument();
  });

  it('renders error state when update check failed', () => {
    render(
      <VersionModal
        isOpen={true}
        onClose={() => undefined}
        updateInfo={{
          ...baseUpdateInfo,
          error: 'network error',
        }}
      />
    );

    expect(screen.getByText('Update Check Failed')).toBeInTheDocument();
    expect(screen.getByText('network error')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    const { container } = render(
      <VersionModal
        isOpen={false}
        onClose={() => undefined}
        updateInfo={baseUpdateInfo}
      />
    );
    expect(container.firstChild).toBeNull();
  });
});
