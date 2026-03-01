import React from 'react';
import type { HostDetailed, ManagedInterface, NicGroup } from './infrastructureTypes';

interface NicGroupCreateModalProps {
  hosts: HostDetailed[];
  newNicGroupHostId: string;
  setNewNicGroupHostId: (v: string) => void;
  newNicGroupName: string;
  setNewNicGroupName: (v: string) => void;
  newNicGroupDescription: string;
  setNewNicGroupDescription: (v: string) => void;
  creatingNicGroup: boolean;
  onCreate: () => void;
  onClose: () => void;
}

export const NicGroupCreateModal: React.FC<NicGroupCreateModalProps> = ({
  hosts,
  newNicGroupHostId,
  setNewNicGroupHostId,
  newNicGroupName,
  setNewNicGroupName,
  newNicGroupDescription,
  setNewNicGroupDescription,
  creatingNicGroup,
  onCreate,
  onClose,
}) => {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="p-6 border-b border-stone-200 dark:border-stone-800">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
              <i className="fa-solid fa-layer-group text-sage-600 dark:text-sage-400"></i>
              Create NIC Group
            </h2>
            <button
              onClick={onClose}
              className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
            >
              <i className="fa-solid fa-times text-lg"></i>
            </button>
          </div>
        </div>

        <div className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Host
            </label>
            <select
              value={newNicGroupHostId}
              onChange={(e) => setNewNicGroupHostId(e.target.value)}
              className="w-full px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
            >
              <option value="">Select a host...</option>
              {hosts.map(host => (
                <option key={host.id} value={host.id}>
                  {host.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Group Name
            </label>
            <input
              type="text"
              value={newNicGroupName}
              onChange={(e) => setNewNicGroupName(e.target.value)}
              placeholder="e.g. uplink-a"
              className="w-full px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Description
            </label>
            <input
              type="text"
              value={newNicGroupDescription}
              onChange={(e) => setNewNicGroupDescription(e.target.value)}
              placeholder="Optional"
              className="w-full px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
            />
          </div>
        </div>

        <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 glass-control text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={onCreate}
            disabled={!newNicGroupHostId || !newNicGroupName.trim() || creatingNicGroup}
            className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
              newNicGroupHostId && newNicGroupName.trim() && !creatingNicGroup
                ? 'bg-sage-600 hover:bg-sage-700 text-white'
                : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
            }`}
          >
            {creatingNicGroup ? (
              <>
                <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                Creating...
              </>
            ) : (
              <>
                <i className="fa-solid fa-check mr-2"></i>
                Create
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

interface NicGroupMemberModalProps {
  memberGroup: NicGroup;
  managedInterfaces: ManagedInterface[];
  memberInterfaceId: string;
  setMemberInterfaceId: (v: string) => void;
  memberRole: string;
  setMemberRole: (v: string) => void;
  addingNicGroupMember: boolean;
  onAdd: () => void;
  onClose: () => void;
}

export const NicGroupMemberModal: React.FC<NicGroupMemberModalProps> = ({
  memberGroup,
  managedInterfaces,
  memberInterfaceId,
  setMemberInterfaceId,
  memberRole,
  setMemberRole,
  addingNicGroupMember,
  onAdd,
  onClose,
}) => {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="p-6 border-b border-stone-200 dark:border-stone-800">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
              <i className="fa-solid fa-plug text-sage-600 dark:text-sage-400"></i>
              Add NIC Group Member
            </h2>
            <button
              onClick={onClose}
              className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
            >
              <i className="fa-solid fa-times text-lg"></i>
            </button>
          </div>
        </div>

        <div className="p-6 space-y-4">
          <div className="text-xs text-stone-500 dark:text-stone-400">
            Group: <span className="text-stone-700 dark:text-stone-300 font-medium">{memberGroup.name}</span>
          </div>

          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Managed Interface
            </label>
            <select
              value={memberInterfaceId}
              onChange={(e) => setMemberInterfaceId(e.target.value)}
              className="w-full px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
            >
              <option value="">Select an interface...</option>
              {managedInterfaces
                .filter(i => i.host_id === memberGroup.host_id)
                .map((iface) => (
                  <option key={iface.id} value={iface.id}>
                    {iface.name} ({iface.interface_type}{iface.ip_address ? `, ${iface.ip_address}` : ''})
                  </option>
                ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
              Role
            </label>
            <select
              value={memberRole}
              onChange={(e) => setMemberRole(e.target.value)}
              className="w-full px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
            >
              <option value="transport">transport</option>
              <option value="external">external</option>
              <option value="custom">custom</option>
              <option value="other">other</option>
            </select>
          </div>
        </div>

        <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 glass-control text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={onAdd}
            disabled={!memberInterfaceId || addingNicGroupMember}
            className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
              memberInterfaceId && !addingNicGroupMember
                ? 'bg-sage-600 hover:bg-sage-700 text-white'
                : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
            }`}
          >
            {addingNicGroupMember ? (
              <>
                <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                Adding...
              </>
            ) : (
              <>
                <i className="fa-solid fa-check mr-2"></i>
                Add Member
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
