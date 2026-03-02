import { describe, expect, it } from 'vitest';

import {
  getInterfaceTypeBadge,
  getManagedIfaceSyncBadge,
  getMtuSyncStatusBadge,
  getPathBadge,
  getStatusBadgeStyle,
} from './badgeHelpers';

describe('badgeHelpers', () => {
  it('maps MTU sync statuses to expected badges', () => {
    expect(getMtuSyncStatusBadge('synced')).toMatchObject({ icon: 'fa-check', text: 'Synced' });
    expect(getMtuSyncStatusBadge('mismatch')).toMatchObject({ icon: 'fa-triangle-exclamation', text: 'Mismatch' });
    expect(getMtuSyncStatusBadge('error')).toMatchObject({ icon: 'fa-times-circle', text: 'Error' });
    expect(getMtuSyncStatusBadge('unconfigured')).toMatchObject({ icon: 'fa-minus', text: 'Not Configured' });
    expect(getMtuSyncStatusBadge('unexpected')).toMatchObject({ icon: 'fa-question', text: 'Unknown' });
  });

  it('maps generic status styles', () => {
    expect(getStatusBadgeStyle('success')).toContain('green');
    expect(getStatusBadgeStyle('failed')).toContain('red');
    expect(getStatusBadgeStyle('pending')).toContain('amber');
    expect(getStatusBadgeStyle('unknown')).toContain('stone');
  });

  it('maps test path badges', () => {
    expect(getPathBadge('data_plane')).toEqual({
      color: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400',
      label: 'Transport',
    });
    expect(getPathBadge('management')).toEqual({
      color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400',
      label: 'Management',
    });
  });

  it('maps interface type badges', () => {
    expect(getInterfaceTypeBadge('transport')).toMatchObject({ text: 'Transport' });
    expect(getInterfaceTypeBadge('external')).toMatchObject({ text: 'External' });
    expect(getInterfaceTypeBadge('uplink')).toMatchObject({ text: 'uplink' });
  });

  it('maps managed-interface sync statuses', () => {
    expect(getManagedIfaceSyncBadge('synced')).toMatchObject({ icon: 'fa-check', text: 'Synced' });
    expect(getManagedIfaceSyncBadge('provisioning')).toMatchObject({ icon: 'fa-spinner fa-spin', text: 'Provisioning' });
    expect(getManagedIfaceSyncBadge('error')).toMatchObject({ icon: 'fa-times-circle', text: 'Error' });
    expect(getManagedIfaceSyncBadge('unconfigured')).toMatchObject({ icon: 'fa-minus', text: 'Pending' });
    expect(getManagedIfaceSyncBadge('drifted')).toMatchObject({ icon: 'fa-question', text: 'drifted' });
  });
});
