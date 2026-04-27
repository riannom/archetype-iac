import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { HostDetailed, NicGroup } from './infrastructureTypes';
import { useNicGroups } from './useNicGroups';

const addNotification = vi.fn();
const apiRequest = vi.fn();

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({ addNotification }),
}));

vi.mock('../../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

describe('useNicGroups', () => {
  const hosts = [
    { id: 'host-1', name: 'agent-1' } as HostDetailed,
    { id: 'host-2', name: 'agent-2' } as HostDetailed,
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('opens and resets the NIC group modal state', () => {
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    act(() => {
      result.current.openNicGroupModal();
    });

    expect(result.current.showNicGroupModal).toBe(true);
    expect(result.current.newNicGroupHostId).toBe('host-1');

    act(() => {
      result.current.setNewNicGroupName('Edge Group');
      result.current.setNewNicGroupDescription('Description');
      result.current.closeNicGroupModal();
    });

    expect(result.current.showNicGroupModal).toBe(false);
    expect(result.current.newNicGroupHostId).toBe('');
    expect(result.current.newNicGroupName).toBe('');
    expect(result.current.newNicGroupDescription).toBe('');
  });

  it('creates a NIC group and refreshes data', async () => {
    apiRequest.mockResolvedValueOnce({ id: 'group-1' });
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    act(() => {
      result.current.openNicGroupModal();
      result.current.setNewNicGroupHostId('host-2');
      result.current.setNewNicGroupName('  Transport Group  ');
      result.current.setNewNicGroupDescription('  MTU lanes  ');
    });

    await act(async () => {
      await result.current.createNicGroup();
    });

    expect(apiRequest).toHaveBeenCalledWith('/infrastructure/hosts/host-2/nic-groups', {
      method: 'POST',
      body: JSON.stringify({ name: 'Transport Group', description: 'MTU lanes' }),
    });
    expect(loadNicGroups).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(result.current.showNicGroupModal).toBe(false);
    });
  });

  it('surfaces create errors via notifications', async () => {
    apiRequest.mockRejectedValueOnce(new Error('create failed'));
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    act(() => {
      result.current.openNicGroupModal();
      result.current.setNewNicGroupHostId('host-1');
      result.current.setNewNicGroupName('Group A');
    });

    await act(async () => {
      await result.current.createNicGroup();
    });

    expect(addNotification).toHaveBeenCalledWith('error', 'Failed to create NIC group', 'create failed');
    expect(loadNicGroups).not.toHaveBeenCalled();
  });

  it('surfaces add-member errors via notifications', async () => {
    apiRequest.mockRejectedValueOnce(new Error('member add failed'));
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    const group = {
      id: 'group-1',
      host_id: 'host-1',
      host_name: 'agent-1',
      name: 'Transport',
      description: null,
      created_at: '',
      updated_at: '',
      members: [],
    } as NicGroup;

    act(() => {
      result.current.openNicGroupMemberModal(group);
      result.current.setMemberInterfaceId('iface-1');
    });

    await act(async () => {
      await result.current.addNicGroupMember();
    });

    expect(addNotification).toHaveBeenCalledWith(
      'error',
      'Failed to add NIC group member',
      'member add failed',
    );
    expect(loadNicGroups).not.toHaveBeenCalled();
    // Modal stays open so the user can retry / fix the input
    expect(result.current.showNicGroupMemberModal).toBe(true);
  });

  it('does nothing when addNicGroupMember is called without a target group or interface', async () => {
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    // No openNicGroupMemberModal → memberGroup is undefined; should early-return
    await act(async () => {
      await result.current.addNicGroupMember();
    });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('emits null role when memberRole is empty', async () => {
    apiRequest.mockResolvedValueOnce({ id: 'member-2' });
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    const group = {
      id: 'group-1',
      host_id: 'host-1',
      host_name: 'agent-1',
      name: 'Transport',
      description: null,
      created_at: '',
      updated_at: '',
      members: [],
    } as NicGroup;

    act(() => {
      result.current.openNicGroupMemberModal(group);
      result.current.setMemberInterfaceId('iface-1');
      result.current.setMemberRole('');
    });

    await act(async () => {
      await result.current.addNicGroupMember();
    });

    expect(apiRequest).toHaveBeenCalledWith(
      '/infrastructure/nic-groups/group-1/members',
      {
        method: 'POST',
        body: JSON.stringify({ managed_interface_id: 'iface-1', role: null }),
      },
    );
  });

  it('falls back to empty host id when hosts list is empty', () => {
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups([], [], loadNicGroups));

    act(() => {
      result.current.openNicGroupModal();
    });

    expect(result.current.newNicGroupHostId).toBe('');
    expect(result.current.showNicGroupModal).toBe(true);
  });

  it('elides error message when the rejection is not an Error instance', async () => {
    apiRequest.mockRejectedValueOnce('plain string error');
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    act(() => {
      result.current.openNicGroupModal();
      result.current.setNewNicGroupHostId('host-1');
      result.current.setNewNicGroupName('Group A');
    });

    await act(async () => {
      await result.current.createNicGroup();
    });

    expect(addNotification).toHaveBeenCalledWith(
      'error',
      'Failed to create NIC group',
      undefined,
    );
  });

  it('does nothing when createNicGroup is called without host or trimmed name', async () => {
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    // Empty state — no host, no name
    await act(async () => {
      await result.current.createNicGroup();
    });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('omits description body when it is whitespace only', async () => {
    apiRequest.mockResolvedValueOnce({ id: 'g' });
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    act(() => {
      result.current.openNicGroupModal();
      result.current.setNewNicGroupHostId('host-1');
      result.current.setNewNicGroupName('G');
      result.current.setNewNicGroupDescription('   ');
    });

    await act(async () => {
      await result.current.createNicGroup();
    });

    expect(apiRequest).toHaveBeenCalledWith(
      '/infrastructure/hosts/host-1/nic-groups',
      {
        method: 'POST',
        body: JSON.stringify({ name: 'G', description: null }),
      },
    );
  });

  it('adds a NIC group member and resets member modal', async () => {
    apiRequest.mockResolvedValueOnce({ id: 'member-1' });
    const loadNicGroups = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useNicGroups(hosts, [], loadNicGroups));

    const group = {
      id: 'group-1',
      host_id: 'host-1',
      host_name: 'agent-1',
      name: 'Transport',
      description: null,
      created_at: '',
      updated_at: '',
      members: [],
    } as NicGroup;

    act(() => {
      result.current.openNicGroupMemberModal(group);
      result.current.setMemberInterfaceId('iface-1');
      result.current.setMemberRole('external');
    });

    await act(async () => {
      await result.current.addNicGroupMember();
    });

    expect(apiRequest).toHaveBeenCalledWith('/infrastructure/nic-groups/group-1/members', {
      method: 'POST',
      body: JSON.stringify({ managed_interface_id: 'iface-1', role: 'external' }),
    });
    expect(loadNicGroups).toHaveBeenCalledTimes(1);
    expect(result.current.showNicGroupMemberModal).toBe(false);
    expect(result.current.memberGroup).toBeNull();
    expect(result.current.memberInterfaceId).toBe('');
    expect(result.current.memberRole).toBe('transport');
  });
});
