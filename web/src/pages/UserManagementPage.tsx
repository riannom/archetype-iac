import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser, type GlobalRole, type User } from '../contexts/UserContext';
import { canManageUsers } from '../utils/permissions';
import { apiRequest } from '../api';
import { ArchetypeIcon } from '../components/icons';
import AdminMenuButton from '../components/AdminMenuButton';
import { formatDate } from '../utils/format';
import UserCreateModal from './UserCreateModal';
import UserEditModal from './UserEditModal';
import UserPasswordModal from './UserPasswordModal';

// ============================================================================
// Types
// ============================================================================

interface UsersResponse {
  users: User[];
  total: number;
}

type ModalType = 'create' | 'edit' | 'password' | null;

// ============================================================================
// Helpers
// ============================================================================

function getRoleBadgeClasses(role: GlobalRole): string {
  switch (role) {
    case 'super_admin':
      return 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 border-purple-200 dark:border-purple-700';
    case 'admin':
      return 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-700';
    case 'operator':
      return 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-700';
    case 'viewer':
      return 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 border-stone-200 dark:border-stone-700';
    default:
      return 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 border-stone-200 dark:border-stone-700';
  }
}

function formatRoleLabel(role: GlobalRole): string {
  switch (role) {
    case 'super_admin': return 'Super Admin';
    case 'admin': return 'Admin';
    case 'operator': return 'Operator';
    case 'viewer': return 'Viewer';
    default: return role;
  }
}


// ============================================================================
// Component
// ============================================================================

const UserManagementPage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user: currentUser, loading: userLoading } = useUser();
  const navigate = useNavigate();

  // Data state
  const [users, setUsers] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showThemeSelector, setShowThemeSelector] = useState(false);

  // Modal state
  const [activeModal, setActiveModal] = useState<ModalType>(null);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);

  // Toggle loading
  const [togglingUser, setTogglingUser] = useState<string | null>(null);

  // ============================================================================
  // Data Loading
  // ============================================================================

  const loadUsers = useCallback(async () => {
    try {
      const data = await apiRequest<UsersResponse>('/users');
      const safeUsers = Array.isArray((data as any)?.users) ? (data as any).users : [];
      setUsers(safeUsers);
      setTotal(typeof (data as any)?.total === 'number' ? (data as any).total : safeUsers.length);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (currentUser?.id) {
      loadUsers();
    }
  }, [currentUser?.id, loadUsers]);

  // ============================================================================
  // Modal Helpers
  // ============================================================================

  const closeModal = () => {
    setActiveModal(null);
    setSelectedUser(null);
  };

  const openCreateModal = () => {
    setActiveModal('create');
    setSelectedUser(null);
  };

  const openEditModal = (u: User) => {
    setSelectedUser(u);
    setActiveModal('edit');
  };

  const openPasswordModal = (u: User) => {
    setSelectedUser(u);
    setActiveModal('password');
  };

  // ============================================================================
  // Actions
  // ============================================================================

  const handleToggleActive = async (u: User) => {
    setTogglingUser(u.id);
    try {
      const endpoint = u.is_active ? 'deactivate' : 'activate';
      await apiRequest<{ status: string }>(`/users/${u.id}/${endpoint}`, {
        method: 'POST',
      });
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${u.is_active ? 'deactivate' : 'activate'} user`);
    } finally {
      setTogglingUser(null);
    }
  };

  // ============================================================================
  // Guards
  // ============================================================================

  if (!userLoading && (!currentUser || !canManageUsers(currentUser))) {
    return <Navigate to="/" replace />;
  }

  // ============================================================================
  // Render
  // ============================================================================

  return (
    <>
      <div className="h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] flex flex-col overflow-hidden">
        {/* Header */}
        <header className="h-16 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10 shrink-0">
          <div className="flex items-center gap-4">
            <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
            <div>
              <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
              <p className="text-[11px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">User Management</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              className="flex items-center gap-2 px-3 py-2 glass-control text-stone-600 dark:text-stone-300 rounded-lg transition-all"
            >
              <i className="fa-solid fa-arrow-left text-xs"></i>
              <span className="text-[11px] font-bold uppercase">Back</span>
            </button>

            <AdminMenuButton />

            <button
              onClick={() => setShowThemeSelector(true)}
              className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all"
              title="Theme Settings"
            >
              <i className="fa-solid fa-palette text-sm"></i>
            </button>

            <button
              onClick={toggleMode}
              className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all"
              title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
            >
              <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
            </button>

          </div>
        </header>

        {/* Content */}
        <div className="flex-1 overflow-y-auto custom-scrollbar p-10">
          <div className="max-w-6xl mx-auto space-y-6">
            {/* Page title + Create button */}
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-bold text-stone-900 dark:text-white">Users</h2>
                <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                  {total} user{total !== 1 ? 's' : ''} registered
                </p>
              </div>
              <button
                onClick={openCreateModal}
                className="flex items-center gap-2 px-4 py-2 bg-sage-600 hover:bg-sage-700 text-white rounded-lg transition-all text-sm font-medium"
              >
                <i className="fa-solid fa-user-plus text-xs"></i>
                Create User
              </button>
            </div>

            {/* Error banner */}
            {error && (
              <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg flex items-center gap-2">
                <i className="fa-solid fa-circle-exclamation text-red-500 flex-shrink-0"></i>
                <span className="text-sm text-red-700 dark:text-red-300">{error}</span>
                <button
                  onClick={() => setError(null)}
                  className="ml-auto text-red-400 hover:text-red-600 dark:hover:text-red-300"
                >
                  <i className="fa-solid fa-times"></i>
                </button>
              </div>
            )}

            {/* Loading state */}
            {loading ? (
              <div className="flex items-center justify-center py-20">
                <div className="flex flex-col items-center gap-3">
                  <i className="fa-solid fa-spinner fa-spin text-sage-500 text-xl" aria-hidden="true"></i>
                  <span className="text-sm text-stone-500 dark:text-stone-400">Loading users...</span>
                </div>
              </div>
            ) : (
              /* Users Table */
              <div className="glass-surface rounded-xl overflow-hidden border">
                <div className="overflow-x-auto table-responsive">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-stone-200 dark:border-stone-700">
                        <th className="text-left py-3 px-4 font-medium text-stone-500 dark:text-stone-400">Username</th>
                        <th className="text-left py-3 px-4 font-medium text-stone-500 dark:text-stone-400">Email</th>
                        <th className="text-left py-3 px-4 font-medium text-stone-500 dark:text-stone-400">Role</th>
                        <th className="text-left py-3 px-4 font-medium text-stone-500 dark:text-stone-400">Status</th>
                        <th className="text-left py-3 px-4 font-medium text-stone-500 dark:text-stone-400">Created</th>
                        <th className="text-right py-3 px-4 font-medium text-stone-500 dark:text-stone-400">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="py-12 text-center text-stone-400 dark:text-stone-500">
                            <i className="fa-solid fa-users text-2xl mb-2 block"></i>
                            No users found.
                          </td>
                        </tr>
                      ) : (
                        users.map((u) => {
                          const isSelf = currentUser?.id === u.id;
                          return (
                            <tr
                              key={u.id}
                              className="border-b border-stone-100 dark:border-stone-800 last:border-b-0 hover:bg-stone-50 dark:hover:bg-stone-800/50 transition-colors"
                            >
                              <td className="py-3 px-4">
                                <div className="flex items-center gap-2">
                                  <div className="w-7 h-7 rounded-full bg-stone-200 dark:bg-stone-700 flex items-center justify-center flex-shrink-0">
                                    <i className="fa-solid fa-user text-xs text-stone-500 dark:text-stone-400"></i>
                                  </div>
                                  <div>
                                    <span className="font-medium text-stone-900 dark:text-white">{u.username}</span>
                                    {isSelf && (
                                      <span className="ml-2 text-[11px] px-1.5 py-0.5 bg-sage-100 dark:bg-sage-900/30 text-sage-600 dark:text-sage-400 border border-sage-200 dark:border-sage-700 rounded font-medium">
                                        You
                                      </span>
                                    )}
                                  </div>
                                </div>
                              </td>
                              <td className="py-3 px-4 text-stone-600 dark:text-stone-400">
                                {u.email || <span className="text-stone-400 dark:text-stone-600 italic">Not set</span>}
                              </td>
                              <td className="py-3 px-4">
                                <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${getRoleBadgeClasses(u.global_role)}`}>
                                  {formatRoleLabel(u.global_role)}
                                </span>
                              </td>
                              <td className="py-3 px-4">
                                <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${
                                  u.is_active
                                    ? 'text-green-600 dark:text-green-400'
                                    : 'text-red-500 dark:text-red-400'
                                }`}>
                                  <div className={`w-1.5 h-1.5 rounded-full ${u.is_active ? 'bg-green-500' : 'bg-red-500'}`}></div>
                                  {u.is_active ? 'Active' : 'Inactive'}
                                </span>
                              </td>
                              <td className="py-3 px-4 text-stone-500 dark:text-stone-400 text-xs">
                                {formatDate(u.created_at)}
                              </td>
                              <td className="py-3 px-4">
                                <div className="flex items-center justify-end gap-1">
                                  <button
                                    onClick={() => openEditModal(u)}
                                    className="p-1.5 text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded transition-colors"
                                    title="Edit user"
                                  >
                                    <i className="fa-solid fa-pen-to-square text-xs"></i>
                                  </button>
                                  <button
                                    onClick={() => openPasswordModal(u)}
                                    className="p-1.5 text-stone-400 hover:text-amber-600 dark:hover:text-amber-400 rounded transition-colors"
                                    title="Reset password"
                                  >
                                    <i className="fa-solid fa-key text-xs"></i>
                                  </button>
                                  {!isSelf && (
                                    <button
                                      onClick={() => handleToggleActive(u)}
                                      disabled={togglingUser === u.id}
                                      className={`p-1.5 rounded transition-colors ${
                                        togglingUser === u.id
                                          ? 'text-stone-300 dark:text-stone-600 cursor-not-allowed'
                                          : u.is_active
                                            ? 'text-stone-400 hover:text-red-500 dark:hover:text-red-400'
                                            : 'text-stone-400 hover:text-green-600 dark:hover:text-green-400'
                                      }`}
                                      title={u.is_active ? 'Deactivate user' : 'Activate user'}
                                    >
                                      {togglingUser === u.id ? (
                                        <i className="fa-solid fa-spinner fa-spin text-xs"></i>
                                      ) : (
                                        <i className={`fa-solid ${u.is_active ? 'fa-user-slash' : 'fa-user-check'} text-xs`}></i>
                                      )}
                                    </button>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <footer className="h-10 border-t border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-center px-10 shrink-0">
          <span className="text-[11px] text-stone-400 dark:text-stone-600 font-medium">
            Archetype User Management
          </span>
        </footer>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />

      <UserCreateModal
        isOpen={activeModal === 'create'}
        onClose={closeModal}
        onCreated={loadUsers}
      />

      <UserEditModal
        isOpen={activeModal === 'edit'}
        user={selectedUser}
        onClose={closeModal}
        onSaved={loadUsers}
      />

      <UserPasswordModal
        isOpen={activeModal === 'password'}
        user={selectedUser}
        onClose={closeModal}
        onReset={loadUsers}
      />
    </>
  );
};

export default UserManagementPage;
