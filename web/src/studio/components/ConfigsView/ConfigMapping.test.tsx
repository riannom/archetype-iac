import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfigMapping } from './ConfigMapping';

const snapshot = {
  id: 's1',
  lab_id: 'lab1',
  node_name: 'orphan1',
  content: 'cfg',
  content_hash: 'h1',
  snapshot_type: 'manual',
  device_kind: 'ceos',
  mapped_to_node_id: null,
  created_at: '2024-01-01T00:00:00Z',
  is_active: false,
  is_orphaned: true,
};

const node = {
  id: 'n1',
  name: 'R1',
  x: 0,
  y: 0,
  nodeType: 'device' as const,
  type: 'router' as const,
  model: 'ceos',
  version: '1',
  container_name: 'r1',
};

describe('ConfigMapping', () => {
  it('shows empty state when no orphaned configs', () => {
    render(
      <ConfigMapping
        labId="lab1"
        orphanedByDeviceKind={new Map()}
        deviceNodes={[]}
        getDisplayName={(n) => n}
        onMapConfig={vi.fn()}
        onSetActiveConfig={vi.fn()}
      />
    );

    expect(screen.getByText('No orphaned configurations found')).toBeInTheDocument();
  });

  it('maps config to compatible node', async () => {
    const onMapConfig = vi.fn().mockResolvedValue({});
    const onSetActiveConfig = vi.fn().mockResolvedValue({});

    const orphaned = new Map([['ceos', [snapshot]]]);

    render(
      <ConfigMapping
        labId="lab1"
        orphanedByDeviceKind={orphaned}
        deviceNodes={[node]}
        getDisplayName={(n) => n}
        onMapConfig={onMapConfig}
        onSetActiveConfig={onSetActiveConfig}
      />
    );

    fireEvent.click(screen.getByText('orphan1'));
    fireEvent.click(screen.getByText('R1'));

    fireEvent.click(screen.getByText('Confirm'));

    expect(onMapConfig).toHaveBeenCalledWith('s1', 'n1');
    expect(onSetActiveConfig).toHaveBeenCalledWith('r1', 's1');
  });
});
