import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ConfigViewer from './ConfigViewer';

const snapshot = {
  id: 's1',
  lab_id: 'lab1',
  node_name: 'r1',
  content: 'line1\nline2',
  content_hash: 'h1',
  snapshot_type: 'manual',
  device_kind: 'ceos',
  mapped_to_node_id: null,
  created_at: '2024-01-01T00:00:00Z',
  is_active: false,
  is_orphaned: false,
};

describe('ConfigViewer', () => {
  it('renders error state', () => {
    render(
      <ConfigViewer
        selectedSnapshot={null}
        comparisonSnapshots={null}
        viewMode="view"
        error="boom"
        labId="lab1"
        studioRequest={vi.fn()}
      />
    );

    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('renders compare empty state', () => {
    render(
      <ConfigViewer
        selectedSnapshot={null}
        comparisonSnapshots={null}
        viewMode="compare"
        error={null}
        labId="lab1"
        studioRequest={vi.fn()}
      />
    );

    expect(screen.getByText('Select 2 snapshots to compare')).toBeInTheDocument();
  });

  it('renders view empty state', () => {
    render(
      <ConfigViewer
        selectedSnapshot={null}
        comparisonSnapshots={null}
        viewMode="view"
        error={null}
        labId="lab1"
        studioRequest={vi.fn()}
      />
    );

    expect(screen.getByText('Select a snapshot to view')).toBeInTheDocument();
  });

  it('renders snapshot and copies to clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(
      <ConfigViewer
        selectedSnapshot={snapshot}
        comparisonSnapshots={null}
        viewMode="view"
        error={null}
        labId="lab1"
        studioRequest={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText('Copy'));
    expect(writeText).toHaveBeenCalledWith('line1\nline2');
  });
});
