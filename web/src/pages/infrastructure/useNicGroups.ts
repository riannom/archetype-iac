import { useState } from 'react';
import { useNotifications } from '../../contexts/NotificationContext';
import { apiRequest } from '../../api';
import type { HostDetailed, NicGroup } from './infrastructureTypes';

export function useNicGroups(
  hosts: HostDetailed[],
  _managedInterfaces: { host_id: string }[],
  loadNicGroups: () => Promise<void>,
) {
  const { addNotification } = useNotifications();

  const notifyError = (title: string, err: unknown) => {
    addNotification('error', title, err instanceof Error ? err.message : undefined);
  };

  const [showNicGroupModal, setShowNicGroupModal] = useState(false);
  const [newNicGroupHostId, setNewNicGroupHostId] = useState<string>('');
  const [newNicGroupName, setNewNicGroupName] = useState<string>('');
  const [newNicGroupDescription, setNewNicGroupDescription] = useState<string>('');
  const [creatingNicGroup, setCreatingNicGroup] = useState(false);
  const [showNicGroupMemberModal, setShowNicGroupMemberModal] = useState(false);
  const [memberGroup, setMemberGroup] = useState<NicGroup | null>(null);
  const [memberInterfaceId, setMemberInterfaceId] = useState<string>('');
  const [memberRole, setMemberRole] = useState<string>('transport');
  const [addingNicGroupMember, setAddingNicGroupMember] = useState(false);

  const openNicGroupModal = () => {
    setShowNicGroupModal(true);
    setNewNicGroupHostId(hosts[0]?.id || '');
    setNewNicGroupName('');
    setNewNicGroupDescription('');
  };

  const closeNicGroupModal = () => {
    setShowNicGroupModal(false);
    setNewNicGroupHostId('');
    setNewNicGroupName('');
    setNewNicGroupDescription('');
  };

  const createNicGroup = async () => {
    if (!newNicGroupHostId || !newNicGroupName.trim()) return;
    setCreatingNicGroup(true);
    try {
      await apiRequest(`/infrastructure/hosts/${newNicGroupHostId}/nic-groups`, {
        method: 'POST',
        body: JSON.stringify({
          name: newNicGroupName.trim(),
          description: newNicGroupDescription.trim() || null,
        }),
      });
      await loadNicGroups();
      closeNicGroupModal();
    } catch (err) {
      notifyError('Failed to create NIC group', err);
    } finally {
      setCreatingNicGroup(false);
    }
  };

  const openNicGroupMemberModal = (group: NicGroup) => {
    setMemberGroup(group);
    setMemberInterfaceId('');
    setMemberRole('transport');
    setShowNicGroupMemberModal(true);
  };

  const closeNicGroupMemberModal = () => {
    setShowNicGroupMemberModal(false);
    setMemberGroup(null);
    setMemberInterfaceId('');
    setMemberRole('transport');
  };

  const addNicGroupMember = async () => {
    if (!memberGroup || !memberInterfaceId) return;
    setAddingNicGroupMember(true);
    try {
      await apiRequest(`/infrastructure/nic-groups/${memberGroup.id}/members`, {
        method: 'POST',
        body: JSON.stringify({
          managed_interface_id: memberInterfaceId,
          role: memberRole || null,
        }),
      });
      await loadNicGroups();
      closeNicGroupMemberModal();
    } catch (err) {
      notifyError('Failed to add NIC group member', err);
    } finally {
      setAddingNicGroupMember(false);
    }
  };

  return {
    showNicGroupModal,
    newNicGroupHostId,
    setNewNicGroupHostId,
    newNicGroupName,
    setNewNicGroupName,
    newNicGroupDescription,
    setNewNicGroupDescription,
    creatingNicGroup,
    showNicGroupMemberModal,
    memberGroup,
    memberInterfaceId,
    setMemberInterfaceId,
    memberRole,
    setMemberRole,
    addingNicGroupMember,
    openNicGroupModal,
    closeNicGroupModal,
    createNicGroup,
    openNicGroupMemberModal,
    closeNicGroupMemberModal,
    addNicGroupMember,
  };
}
