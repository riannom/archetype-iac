import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { NodeList } from './NodeList';

const snapshots = [
  {
    id: 's1',
    lab_id: 'lab1',
    node_name: 'r1',
    content: 'cfg',
    content_hash: 'h1',
    snapshot_type: 'manual',
    device_kind: 'ceos',
    mapped_to_node_id: null,
    created_at: '2024-01-01T00:00:00Z',
    is_active: false,
    is_orphaned: false,
  },
  {
    id: 's2',
    lab_id: 'lab1',
    node_name: 'orph',
    content: 'cfg2',
    content_hash: 'h2',
    snapshot_type: 'manual',
    device_kind: 'iosv',
    mapped_to_node_id: null,
    created_at: '2024-01-02T00:00:00Z',
    is_active: false,
    is_orphaned: true,
  },
];

const deviceNodes = [
  {
    id: 'n1',
    name: 'R1',
    x: 0,
    y: 0,
    nodeType: 'device' as const,
    type: 'router' as const,
    model: 'ceos',
    version: '1',
    container_name: 'r1',
  },
];

describe('NodeList', () => {
  it('renders active nodes and selects', () => {
    const onSelectNode = vi.fn();

    render(
      <NodeList
        nodeNames={['r1']}
        orphanedNodeNames={[]}
        selectedNodeName={null}
        nodeNamesWithSnapshots={['r1']}
        snapshots={snapshots}
        activeNodeNames={new Set(['r1'])}
        runtimeStates={{ n1: 'running' }}
        deviceNodes={deviceNodes}
        getDisplayName={(n) => (n === 'r1' ? 'R1' : n)}
        onSelectNode={onSelectNode}
        onBulkDeleteOrphaned={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText('R1'));
    expect(onSelectNode).toHaveBeenCalledWith('r1');
  });

  it('renders orphaned section and delete all', () => {
    const onBulkDelete = vi.fn();

    render(
      <NodeList
        nodeNames={['r1']}
        orphanedNodeNames={['orph']}
        selectedNodeName={'orph'}
        nodeNamesWithSnapshots={['r1', 'orph']}
        snapshots={snapshots}
        activeNodeNames={new Set(['r1'])}
        runtimeStates={{ n1: 'running' }}
        deviceNodes={deviceNodes}
        getDisplayName={(n) => n}
        onSelectNode={vi.fn()}
        onBulkDeleteOrphaned={onBulkDelete}
      />
    );

    fireEvent.click(screen.getByText('Delete All'));
    expect(onBulkDelete).toHaveBeenCalled();
    expect(screen.getByText('iosv')).toBeInTheDocument();
  });
});
