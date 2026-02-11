import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SnapshotList from './SnapshotList';

const snapshot = {
  id: 's1',
  lab_id: 'lab1',
  node_name: 'r1',
  content: 'cfg',
  content_hash: 'hash1234',
  snapshot_type: 'manual',
  device_kind: 'ceos',
  mapped_to_node_id: null,
  created_at: '2024-01-01T00:00:00Z',
  is_active: false,
  is_orphaned: false,
};

const baseProps = {
  selectedNodeName: 'r1',
  nodeSnapshots: [snapshot],
  selectedSnapshotIds: new Set<string>(),
  viewMode: 'view' as const,
  loading: false,
  getDisplayName: (n: string) => n,
  onSnapshotClick: vi.fn(),
  onDeleteSnapshot: vi.fn(),
  onSetActiveConfig: vi.fn().mockResolvedValue({}),
  onSetViewMode: vi.fn(),
  onDownloadNode: vi.fn(),
};

describe('SnapshotList', () => {
  beforeEach(() => {
    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows empty state when no node selected', () => {
    render(
      <SnapshotList
        {...baseProps}
        selectedNodeName={null}
      />
    );

    expect(screen.getByText('Select a node to view snapshots')).toBeInTheDocument();
  });

  it('shows no snapshots state', () => {
    render(
      <SnapshotList
        {...baseProps}
        nodeSnapshots={[]}
      />
    );

    expect(screen.getByText('No snapshots')).toBeInTheDocument();
  });

  it('handles snapshot click and delete', () => {
    render(<SnapshotList {...baseProps} />);

    fireEvent.click(screen.getByText('hash1234'));
    expect(baseProps.onSnapshotClick).toHaveBeenCalledWith('s1');

    fireEvent.mouseEnter(screen.getByText('hash1234').closest('div')!);
    fireEvent.click(screen.getByTitle('Delete snapshot'));
    expect(baseProps.onDeleteSnapshot).toHaveBeenCalledWith('s1');
  });

  it('sets active config on hover action', async () => {
    render(<SnapshotList {...baseProps} />);

    fireEvent.mouseEnter(screen.getByText('hash1234').closest('div')!);
    fireEvent.click(screen.getByTitle('Set as startup config'));

    expect(baseProps.onSetActiveConfig).toHaveBeenCalledWith('r1', 's1');
  });
});
