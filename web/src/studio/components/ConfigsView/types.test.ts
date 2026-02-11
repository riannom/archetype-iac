import { describe, it, expect } from 'vitest';
import type { ConfigSnapshot } from './types';


describe('ConfigsView types', () => {
  it('allows constructing a ConfigSnapshot shape', () => {
    const snapshot: ConfigSnapshot = {
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
    };

    expect(snapshot.id).toBe('s1');
  });
});
