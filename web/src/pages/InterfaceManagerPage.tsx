import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useUser } from '../contexts/UserContext';
import { apiRequest } from '../api';
import { formatTimestamp } from '../utils/format';

interface AgentManagedInterface {
  id: string;
  host_id: string;
  host_name: string | null;
  name: string;
  interface_type: string;
  parent_interface: string | null;
  vlan_id: number | null;
  ip_address: string | null;
  desired_mtu: number;
  current_mtu: number | null;
  is_up: boolean;
  sync_status: string;
  sync_error: string | null;
  last_sync_at: string | null;
  created_at: string;
  updated_at: string;
}

interface Agent {
  id: string;
  name: string;
  status: string;
  address: string;
}

interface InterfaceDetail {
  name: string;
  mtu: number;
  is_physical: boolean;
  state: string;
}

const CIDR_REGEX = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/;

function isValidCidr(value: string): boolean {
  if (!CIDR_REGEX.test(value)) return false;
  const [ip, prefixStr] = value.split('/');
  const prefix = parseInt(prefixStr);
  if (prefix < 0 || prefix > 32) return false;
  const octets = ip.split('.').map(Number);
  return octets.every(o => o >= 0 && o <= 255);
}

const TYPE_DESCRIPTIONS: Record<string, string> = {
  transport: 'Routed subinterface for VXLAN tunnel underlay',
  external: 'L2 pass-through to OVS bridge for external network access',
  custom: 'General-purpose managed interface',
};

function getSyncBadge(status: string): { text: string; color: string; icon: string } {
  switch (status) {
    case 'synced': return { text: 'Synced', color: 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400', icon: 'fa-check' };
    case 'mismatch': return { text: 'Mismatch', color: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400', icon: 'fa-triangle-exclamation' };
    case 'error': return { text: 'Error', color: 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400', icon: 'fa-xmark' };
    case 'unconfigured': return { text: 'Pending', color: 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400', icon: 'fa-clock' };
    default: return { text: status, color: 'bg-stone-100 dark:bg-stone-800 text-stone-500', icon: 'fa-question' };
  }
}

function getTypeBadge(type: string): { text: string; color: string } {
  switch (type) {
    case 'transport': return { text: 'Transport', color: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400' };
    case 'external': return { text: 'External', color: 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400' };
    case 'custom': return { text: 'Custom', color: 'bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-400' };
    default: return { text: type, color: 'bg-stone-100 dark:bg-stone-800 text-stone-500' };
  }
}

export default function InterfaceManagerPage() {
  const { user } = useUser();
  const navigate = useNavigate();

  // Data state
  const [interfaces, setInterfaces] = useState<AgentManagedInterface[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [filterHost, setFilterHost] = useState('');
  const [filterType, setFilterType] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  // Create modal
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createForm, setCreateForm] = useState({
    host_id: '',
    interface_type: 'custom' as string,
    parent_interface: '',
    vlan_id: '' as string,
    ip_address: '',
    desired_mtu: 9000,
  });
  const [agentInterfaces, setAgentInterfaces] = useState<InterfaceDetail[]>([]);
  const [loadingAgentInterfaces, setLoadingAgentInterfaces] = useState(false);
  const [creating, setCreating] = useState(false);

  // Delete
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Inline edit
  const [editingMtu, setEditingMtu] = useState<string | null>(null);
  const [editMtuValue, setEditMtuValue] = useState('');

  // Edit modal
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingInterface, setEditingInterface] = useState<AgentManagedInterface | null>(null);
  const [editForm, setEditForm] = useState({ ip_address: '', desired_mtu: 9000 });
  const [saving, setSaving] = useState(false);

  if (!user?.is_admin) return <Navigate to="/infrastructure" replace />;

  const loadInterfaces = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (filterHost) params.set('host_id', filterHost);
      if (filterType) params.set('interface_type', filterType);
      const qs = params.toString();
      const data = await apiRequest<{ interfaces: AgentManagedInterface[] }>(`/infrastructure/interfaces${qs ? '?' + qs : ''}`);
      setInterfaces(data.interfaces || []);
    } catch (err) {
      console.error('Failed to load interfaces:', err);
    } finally {
      setLoading(false);
    }
  }, [filterHost, filterType]);

  const loadAgents = useCallback(async () => {
    try {
      const data = await apiRequest<Agent[]>('/agents');
      setAgents(data || []);
    } catch (err) {
      console.error('Failed to load agents:', err);
    }
  }, []);

  useEffect(() => { loadAgents(); }, [loadAgents]);
  useEffect(() => { loadInterfaces(); }, [loadInterfaces]);

  const loadAgentPhysicalInterfaces = async (hostId: string) => {
    if (!hostId) { setAgentInterfaces([]); return; }
    try {
      setLoadingAgentInterfaces(true);
      const data = await apiRequest<{ interfaces: InterfaceDetail[] }>(`/infrastructure/agents/${hostId}/interfaces`);
      setAgentInterfaces((data.interfaces || []).filter((i: InterfaceDetail) => i.is_physical));
    } catch {
      setAgentInterfaces([]);
    } finally {
      setLoadingAgentInterfaces(false);
    }
  };

  const handleCreateHostChange = (hostId: string) => {
    setCreateForm(f => ({ ...f, host_id: hostId, parent_interface: '' }));
    loadAgentPhysicalInterfaces(hostId);
  };

  const handleCreate = async () => {
    if (!createForm.host_id) return;
    try {
      setCreating(true);
      await apiRequest(`/infrastructure/agents/${createForm.host_id}/managed-interfaces`, {
        method: 'POST',
        body: JSON.stringify({
          interface_type: createForm.interface_type,
          parent_interface: createForm.parent_interface || undefined,
          vlan_id: createForm.vlan_id ? parseInt(createForm.vlan_id) : undefined,
          ip_address: createForm.ip_address || undefined,
          desired_mtu: createForm.desired_mtu,
          attach_to_ovs: createForm.interface_type === 'external',
        }),
      });
      setShowCreateModal(false);
      setCreateForm({ host_id: '', interface_type: 'custom', parent_interface: '', vlan_id: '', ip_address: '', desired_mtu: 9000 });
      await loadInterfaces();
    } catch (err) {
      console.error('Failed to create interface:', err);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      setDeletingId(id);
      await apiRequest(`/infrastructure/interfaces/${id}`, { method: 'DELETE' });
      setConfirmDelete(null);
      await loadInterfaces();
    } catch (err) {
      console.error('Failed to delete interface:', err);
    } finally {
      setDeletingId(null);
    }
  };

  const handleEdit = (iface: AgentManagedInterface) => {
    setEditingInterface(iface);
    setEditForm({
      ip_address: iface.ip_address || '',
      desired_mtu: iface.desired_mtu,
    });
    setShowEditModal(true);
  };

  const handleEditSave = async () => {
    if (!editingInterface) return;
    try {
      setSaving(true);
      await apiRequest(`/infrastructure/interfaces/${editingInterface.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          desired_mtu: editForm.desired_mtu,
          ip_address: editForm.ip_address || null,
        }),
      });
      setShowEditModal(false);
      setEditingInterface(null);
      await loadInterfaces();
    } catch (err) {
      console.error('Failed to update interface:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleMtuSave = async (id: string) => {
    const mtu = parseInt(editMtuValue);
    if (isNaN(mtu) || mtu < 68 || mtu > 9216) return;
    try {
      await apiRequest(`/infrastructure/interfaces/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ desired_mtu: mtu }),
      });
      setEditingMtu(null);
      await loadInterfaces();
    } catch (err) {
      console.error('Failed to update MTU:', err);
    }
  };

  // Filter logic
  const filtered = interfaces.filter(iface => {
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      if (!iface.name.toLowerCase().includes(q) &&
          !(iface.host_name || '').toLowerCase().includes(q) &&
          !(iface.ip_address || '').toLowerCase().includes(q)) {
        return false;
      }
    }
    return true;
  });

  // Group by host
  const grouped: Record<string, AgentManagedInterface[]> = {};
  for (const iface of filtered) {
    const key = iface.host_id;
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(iface);
  }

  return (
    <div className="min-h-screen bg-stone-100 dark:bg-stone-950 flex flex-col">
      {/* Header */}
      <header className="bg-white dark:bg-stone-900 border-b border-stone-200 dark:border-stone-800 px-10 py-6">
        <div className="max-w-7xl mx-auto">
          <div className="flex items-center gap-3 mb-2">
            <button
              onClick={() => navigate('/infrastructure?tab=network')}
              className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
            >
              <i className="fa-solid fa-arrow-left"></i>
            </button>
            <h1 className="text-2xl font-bold text-stone-900 dark:text-white">Interface Manager</h1>
          </div>
          <p className="text-stone-500 text-sm ml-7">
            Manage provisioned network interfaces across all agent hosts.
          </p>
        </div>
      </header>

      {/* Filters + Actions */}
      <div className="px-10 py-4 border-b border-stone-200 dark:border-stone-800 bg-white dark:bg-stone-900">
        <div className="max-w-7xl mx-auto flex items-center gap-4 flex-wrap">
          <select
            value={filterHost}
            onChange={e => setFilterHost(e.target.value)}
            className="px-3 py-1.5 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
          >
            <option value="">All Hosts</option>
            {agents.map(a => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          <select
            value={filterType}
            onChange={e => setFilterType(e.target.value)}
            className="px-3 py-1.5 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
          >
            <option value="">All Types</option>
            <option value="transport">Transport</option>
            <option value="external">External</option>
            <option value="custom">Custom</option>
          </select>
          <div className="relative flex-1 min-w-[200px]">
            <i className="fa-solid fa-search absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-xs"></i>
            <input
              type="text"
              placeholder="Search interfaces..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
            />
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <button
              onClick={loadInterfaces}
              disabled={loading}
              className="px-3 py-1.5 text-sm rounded-lg border border-stone-300 dark:border-stone-700 hover:bg-stone-50 dark:hover:bg-stone-800 text-stone-600 dark:text-stone-400 transition-colors"
            >
              <i className={`fa-solid fa-sync ${loading ? 'fa-spin' : ''} mr-1.5`}></i>
              Refresh
            </button>
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-3 py-1.5 text-sm rounded-lg bg-sage-600 hover:bg-sage-700 text-white font-medium transition-colors"
            >
              <i className="fa-solid fa-plus mr-1.5"></i>
              Create Interface
            </button>
          </div>
        </div>
      </div>

      {/* Content */}
      <main className="flex-1 overflow-y-auto p-10">
        <div className="max-w-7xl mx-auto">
          {loading && interfaces.length === 0 ? (
            <div className="flex items-center justify-center py-20">
              <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-20">
              <i className="fa-solid fa-ethernet text-stone-300 dark:text-stone-700 text-5xl mb-4"></i>
              <p className="text-stone-500 dark:text-stone-400 text-lg">No managed interfaces</p>
              <p className="text-stone-400 dark:text-stone-500 text-sm mt-1">
                Create a transport or external interface to get started.
              </p>
            </div>
          ) : (
            Object.entries(grouped).map(([hostId, hostInterfaces]) => {
              const hostName = hostInterfaces[0]?.host_name || agents.find(a => a.id === hostId)?.name || hostId;
              return (
                <div key={hostId} className="mb-6">
                  <div className="flex items-center gap-2 mb-3">
                    <i className="fa-solid fa-server text-stone-400"></i>
                    <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300">{hostName}</h3>
                    <span className="text-xs text-stone-400">({hostInterfaces.length} interface{hostInterfaces.length !== 1 ? 's' : ''})</span>
                  </div>
                  <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-800 rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-stone-200 dark:border-stone-700 bg-stone-50 dark:bg-stone-800/50">
                          <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Interface</th>
                          <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Type</th>
                          <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Parent</th>
                          <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">VLAN</th>
                          <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">IP Address</th>
                          <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">MTU</th>
                          <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Status</th>
                          <th className="text-right py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {hostInterfaces.map(iface => {
                          const syncBadge = getSyncBadge(iface.sync_status);
                          const typeBadge = getTypeBadge(iface.interface_type);
                          const isEditingMtu = editingMtu === iface.id;

                          return (
                            <tr key={iface.id} className="border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/30">
                              <td className="py-2 px-3">
                                <div className="flex items-center gap-2">
                                  <div className={`w-2 h-2 rounded-full ${iface.is_up ? 'bg-green-500' : 'bg-stone-400'}`}></div>
                                  <span className="font-mono text-xs font-medium text-stone-700 dark:text-stone-300">{iface.name}</span>
                                </div>
                              </td>
                              <td className="py-2 px-3">
                                <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${typeBadge.color}`}>
                                  {typeBadge.text}
                                </span>
                              </td>
                              <td className="py-2 px-3 font-mono text-xs text-stone-500">{iface.parent_interface || '-'}</td>
                              <td className="py-2 px-3 text-xs text-stone-500">{iface.vlan_id ?? '-'}</td>
                              <td className="py-2 px-3 font-mono text-xs text-stone-600 dark:text-stone-400">{iface.ip_address || '-'}</td>
                              <td className="py-2 px-3">
                                {isEditingMtu ? (
                                  <input
                                    type="number"
                                    value={editMtuValue}
                                    onChange={e => setEditMtuValue(e.target.value)}
                                    onKeyDown={e => {
                                      if (e.key === 'Enter') handleMtuSave(iface.id);
                                      if (e.key === 'Escape') setEditingMtu(null);
                                    }}
                                    onBlur={() => setEditingMtu(null)}
                                    autoFocus
                                    className="w-20 px-1 py-0.5 text-xs font-mono rounded border border-sage-400 dark:border-sage-600 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
                                    min={68} max={9216}
                                  />
                                ) : (
                                  <div className="flex items-center gap-1">
                                    <span className="font-mono text-xs text-stone-500">{iface.current_mtu ?? '-'}</span>
                                    <span className="text-stone-300 dark:text-stone-600">/</span>
                                    <button
                                      className="font-mono text-xs text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 cursor-pointer"
                                      onClick={() => { setEditingMtu(iface.id); setEditMtuValue(String(iface.desired_mtu)); }}
                                      title="Click to edit desired MTU"
                                    >
                                      {iface.desired_mtu}
                                    </button>
                                  </div>
                                )}
                              </td>
                              <td className="py-2 px-3">
                                <div className="flex flex-col gap-0.5">
                                  <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium w-fit ${syncBadge.color}`}>
                                    <i className={`fa-solid ${syncBadge.icon} text-[9px]`}></i>
                                    {syncBadge.text}
                                  </span>
                                  {iface.sync_error && (
                                    <span className="text-[11px] text-red-500 dark:text-red-400 max-w-[200px] truncate" title={iface.sync_error}>
                                      {iface.sync_error}
                                    </span>
                                  )}
                                </div>
                              </td>
                              <td className="py-2 px-3 text-right">
                                {confirmDelete === iface.id ? (
                                  <div className="flex items-center gap-1 justify-end">
                                    <button
                                      onClick={() => handleDelete(iface.id)}
                                      disabled={deletingId === iface.id}
                                      className="px-2 py-0.5 text-xs rounded bg-red-500 hover:bg-red-600 text-white"
                                    >
                                      {deletingId === iface.id ? <i className="fa-solid fa-spinner fa-spin"></i> : 'Delete'}
                                    </button>
                                    <button
                                      onClick={() => setConfirmDelete(null)}
                                      className="px-2 py-0.5 text-xs rounded bg-stone-200 dark:bg-stone-700 text-stone-600 dark:text-stone-400"
                                    >
                                      Cancel
                                    </button>
                                  </div>
                                ) : (
                                  <div className="flex items-center gap-1 justify-end">
                                    <button
                                      onClick={() => handleEdit(iface)}
                                      className="px-2 py-0.5 text-xs rounded text-stone-400 hover:text-sage-600 hover:bg-sage-50 dark:hover:bg-sage-900/20 transition-colors"
                                      title="Edit interface"
                                    >
                                      <i className="fa-solid fa-pen-to-square"></i>
                                    </button>
                                    <button
                                      onClick={() => setConfirmDelete(iface.id)}
                                      className="px-2 py-0.5 text-xs rounded text-stone-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                                      title="Delete interface"
                                    >
                                      <i className="fa-solid fa-trash"></i>
                                    </button>
                                  </div>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </main>

      {/* Edit Modal */}
      {showEditModal && editingInterface && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white dark:bg-stone-900 rounded-2xl border border-stone-200 dark:border-stone-800 w-full max-w-lg shadow-xl">
            <div className="px-6 py-4 border-b border-stone-200 dark:border-stone-800">
              <h3 className="text-lg font-semibold text-stone-900 dark:text-white">Edit Interface</h3>
              <p className="text-sm text-stone-500 dark:text-stone-400 mt-0.5 font-mono">{editingInterface.name}</p>
            </div>
            <div className="px-6 py-4 space-y-4">
              {/* IP Address */}
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1">IP Address (CIDR)</label>
                <input
                  type="text"
                  value={editForm.ip_address}
                  onChange={e => setEditForm(f => ({ ...f, ip_address: e.target.value }))}
                  placeholder="e.g. 10.100.0.1/24"
                  className={`w-full px-3 py-2 text-sm rounded-lg border bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300 ${
                    editForm.ip_address && !isValidCidr(editForm.ip_address)
                      ? 'border-amber-400 dark:border-amber-600'
                      : 'border-stone-300 dark:border-stone-700'
                  }`}
                />
                {editForm.ip_address && !isValidCidr(editForm.ip_address) && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                    <i className="fa-solid fa-triangle-exclamation mr-1"></i>
                    Must be valid CIDR notation (e.g. 10.100.0.1/24)
                  </p>
                )}
              </div>
              {/* MTU */}
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1">Desired MTU</label>
                <input
                  type="number"
                  value={editForm.desired_mtu}
                  onChange={e => setEditForm(f => ({ ...f, desired_mtu: parseInt(e.target.value) || 9000 }))}
                  min={68} max={9216}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
                />
              </div>
              {/* Current status info */}
              {editingInterface.sync_error && (
                <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
                  <div className="flex items-start gap-2">
                    <i className="fa-solid fa-circle-exclamation text-red-500 text-xs mt-0.5"></i>
                    <div>
                      <p className="text-xs font-medium text-red-700 dark:text-red-400">Last sync error</p>
                      <p className="text-xs text-red-600 dark:text-red-400/80 mt-0.5">{editingInterface.sync_error}</p>
                    </div>
                  </div>
                </div>
              )}
            </div>
            <div className="px-6 py-4 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
              <button
                onClick={() => { setShowEditModal(false); setEditingInterface(null); }}
                className="px-4 py-2 text-sm rounded-lg border border-stone-300 dark:border-stone-700 text-stone-600 dark:text-stone-400 hover:bg-stone-50 dark:hover:bg-stone-800"
              >
                Cancel
              </button>
              <button
                onClick={handleEditSave}
                disabled={saving || (!!editForm.ip_address && !isValidCidr(editForm.ip_address))}
                className="px-4 py-2 text-sm rounded-lg bg-sage-600 hover:bg-sage-700 text-white font-medium disabled:opacity-50 transition-colors"
              >
                {saving ? <i className="fa-solid fa-spinner fa-spin mr-1.5"></i> : <i className="fa-solid fa-check mr-1.5"></i>}
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white dark:bg-stone-900 rounded-2xl border border-stone-200 dark:border-stone-800 w-full max-w-lg shadow-xl">
            <div className="px-6 py-4 border-b border-stone-200 dark:border-stone-800">
              <h3 className="text-lg font-semibold text-stone-900 dark:text-white">Create Managed Interface</h3>
            </div>
            <div className="px-6 py-4 space-y-4">
              {/* Host */}
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1">Host</label>
                <select
                  value={createForm.host_id}
                  onChange={e => handleCreateHostChange(e.target.value)}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
                >
                  <option value="">Select host...</option>
                  {agents.filter(a => a.status === 'online').map(a => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </div>

              {/* Type */}
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1">Type</label>
                <select
                  value={createForm.interface_type}
                  onChange={e => {
                    const type = e.target.value;
                    setCreateForm(f => ({ ...f, interface_type: type, ...(type === 'external' ? { ip_address: '' } : {}) }));
                  }}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
                >
                  <option value="transport">Transport (data plane)</option>
                  <option value="external">External (connectivity)</option>
                  <option value="custom">Custom</option>
                </select>
                <p className="text-xs text-stone-400 dark:text-stone-500 mt-1">{TYPE_DESCRIPTIONS[createForm.interface_type]}</p>
              </div>

              {/* Parent Interface */}
              <div>
                <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1">Parent Interface</label>
                <select
                  value={createForm.parent_interface}
                  onChange={e => setCreateForm(f => ({ ...f, parent_interface: e.target.value }))}
                  disabled={!createForm.host_id || loadingAgentInterfaces}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300 disabled:opacity-50"
                >
                  <option value="">{loadingAgentInterfaces ? 'Loading...' : 'Select interface...'}</option>
                  {agentInterfaces.map(i => (
                    <option key={i.name} value={i.name}>{i.name} (MTU: {i.mtu}, {i.state})</option>
                  ))}
                </select>
              </div>

              {/* VLAN ID + IP side by side */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1">VLAN ID</label>
                  <input
                    type="number"
                    value={createForm.vlan_id}
                    onChange={e => setCreateForm(f => ({ ...f, vlan_id: e.target.value }))}
                    placeholder="e.g. 100"
                    min={1} max={4094}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1">MTU</label>
                  <input
                    type="number"
                    value={createForm.desired_mtu}
                    onChange={e => setCreateForm(f => ({ ...f, desired_mtu: parseInt(e.target.value) || 9000 }))}
                    min={68} max={9216}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300"
                  />
                </div>
              </div>

              {/* IP - hidden for external type */}
              {createForm.interface_type !== 'external' && (
                <div>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-1">IP Address (CIDR)</label>
                  <input
                    type="text"
                    value={createForm.ip_address}
                    onChange={e => setCreateForm(f => ({ ...f, ip_address: e.target.value }))}
                    placeholder="e.g. 10.100.0.1/24"
                    className={`w-full px-3 py-2 text-sm rounded-lg border bg-white dark:bg-stone-800 text-stone-700 dark:text-stone-300 ${
                      createForm.ip_address && !isValidCidr(createForm.ip_address)
                        ? 'border-amber-400 dark:border-amber-600'
                        : 'border-stone-300 dark:border-stone-700'
                    }`}
                  />
                  {createForm.ip_address && !isValidCidr(createForm.ip_address) && (
                    <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                      <i className="fa-solid fa-triangle-exclamation mr-1"></i>
                      Must be valid CIDR notation (e.g. 10.100.0.1/24)
                    </p>
                  )}
                </div>
              )}

            </div>

            <div className="px-6 py-4 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
              <button
                onClick={() => setShowCreateModal(false)}
                className="px-4 py-2 text-sm rounded-lg border border-stone-300 dark:border-stone-700 text-stone-600 dark:text-stone-400 hover:bg-stone-50 dark:hover:bg-stone-800"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!createForm.host_id || creating || (createForm.interface_type !== 'external' && !!createForm.ip_address && !isValidCidr(createForm.ip_address))}
                className="px-4 py-2 text-sm rounded-lg bg-sage-600 hover:bg-sage-700 text-white font-medium disabled:opacity-50 transition-colors"
              >
                {creating ? <i className="fa-solid fa-spinner fa-spin mr-1.5"></i> : <i className="fa-solid fa-plus mr-1.5"></i>}
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
