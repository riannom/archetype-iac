import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser, type GlobalRole, type User } from '../contexts/UserContext';
import { canManageUsers } from '../utils/permissions';
import { apiRequest } from '../api';
import { ArchetypeIcon } from '../components/icons';

// ============================================================================
// Types
// ============================================================================

interface UsersResponse {
  users: User[];
  total: number;
}

interface CreateUserPayload {
  username: string;
  password: string;
  email?: string;
  global_role?: GlobalRole;
}

interface EditUserPayload {
  email?: string;
  global_role?: GlobalRole;
}

type ModalType = 'create' | 'edit' | 'password' | null;

const GLOBAL_ROLES: { value: GlobalRole; label: string; description: string }[] = [
  { value: 'super_admin', label: 'Super Admin', description: 'Full system access' },
  { value: 'admin', label: 'Admin', description: 'Manage users and labs' },
  { value: 'operator', label: 'Operator', description: 'Deploy and manage labs' },
  { value: 'viewer', label: 'Viewer', description: 'Read-only access' },
];

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

function getPasswordStrength(password: string): { label: string; color: string; width: string } {
  if (password.length === 0) return { label: '', color: '', width: 'w-0' };
  if (password.length < 10) return { label: 'Weak', color: 'bg-red-500', width: 'w-1/3' };
  if (password.length < 15) return { label: 'Moderate', color: 'bg-amber-500', width: 'w-2/3' };
  return { label: 'Strong', color: 'bg-green-500', width: 'w-full' };
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return iso;
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
  const [modalError, setModalError] = useState<string | null>(null);
  const [modalLoading, setModalLoading] = useState(false);

  // Create user form
  const [createUsername, setCreateUsername] = useState('');
  const [createPassword, setCreatePassword] = useState('');
  const [createEmail, setCreateEmail] = useState('');
  const [createRole, setCreateRole] = useState<GlobalRole>('viewer');

  // Edit user form
  const [editEmail, setEditEmail] = useState('');
  const [editRole, setEditRole] = useState<GlobalRole>('viewer');

  // Password reset form
  const [newPassword, setNewPassword] = useState('');

  // Toggle loading
  const [togglingUser, setTogglingUser] = useState<string | null>(null);

  // ============================================================================
  // Data Loading
  // ============================================================================

  const loadUsers = useCallback(async () => {
    try {
      const data = await apiRequest<UsersResponse>('/users');
      setUsers(data.users);
      setTotal(data.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (currentUser) {
      loadUsers();
    }
  }, [currentUser, loadUsers]);

  // ============================================================================
  // Modal Helpers
  // ============================================================================

  const resetModalState = () => {
    setActiveModal(null);
    setSelectedUser(null);
    setModalError(null);
    setModalLoading(false);
    setCreateUsername('');
    setCreatePassword('');
    setCreateEmail('');
    setCreateRole('viewer');
    setEditEmail('');
    setEditRole('viewer');
    setNewPassword('');
  };

  const openCreateModal = () => {
    resetModalState();
    setActiveModal('create');
  };

  const openEditModal = (u: User) => {
    resetModalState();
    setSelectedUser(u);
    setEditEmail(u.email || '');
    setEditRole(u.global_role);
    setActiveModal('edit');
  };

  const openPasswordModal = (u: User) => {
    resetModalState();
    setSelectedUser(u);
    setActiveModal('password');
  };

  // ============================================================================
  // Actions
  // ============================================================================

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createUsername.trim() || !createPassword.trim()) {
      setModalError('Username and password are required.');
      return;
    }
    setModalLoading(true);
    setModalError(null);
    try {
      const payload: CreateUserPayload = {
        username: createUsername.trim(),
        password: createPassword,
        global_role: createRole,
      };
      if (createEmail.trim()) {
        payload.email = createEmail.trim();
      }
      await apiRequest<User>('/users', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      resetModalState();
      await loadUsers();
    } catch (err) {
      setModalError(err instanceof Error ? err.message : 'Failed to create user');
    } finally {
      setModalLoading(false);
    }
  };

  const handleEditUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedUser) return;
    setModalLoading(true);
    setModalError(null);
    try {
      const payload: EditUserPayload = {
        email: editEmail.trim() || undefined,
        global_role: editRole,
      };
      await apiRequest<User>(`/users/${selectedUser.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      });
      resetModalState();
      await loadUsers();
    } catch (err) {
      setModalError(err instanceof Error ? err.message : 'Failed to update user');
    } finally {
      setModalLoading(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedUser || !newPassword.trim()) {
      setModalError('Password is required.');
      return;
    }
    setModalLoading(true);
    setModalError(null);
    try {
      await apiRequest<{ status: string }>(`/users/${selectedUser.id}/password`, {
        method: 'PUT',
        body: JSON.stringify({ new_password: newPassword }),
      });
      resetModalState();
    } catch (err) {
      setModalError(err instanceof Error ? err.message : 'Failed to reset password');
    } finally {
      setModalLoading(false);
    }
  };

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

  const passwordStrength = activeModal === 'create'
    ? getPasswordStrength(createPassword)
    : getPasswordStrength(newPassword);
  const activePassword = activeModal === 'create' ? createPassword : newPassword;

  return (
    <>
      <div className="h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] flex flex-col overflow-hidden">
        {/* Header */}
        <header className="h-16 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10 shrink-0">
          <div className="flex items-center gap-4">
            <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
            <div>
              <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">User Management</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            >
              <i className="fa-solid fa-arrow-left text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Back</span>
            </button>

            <button
              onClick={() => setShowThemeSelector(true)}
              className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
              title="Theme Settings"
            >
              <i className="fa-solid fa-palette text-sm"></i>
            </button>

            <button
              onClick={toggleMode}
              className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
              title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
            >
              <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
            </button>

          </div>
        </header>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-10">
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
                <i className="fa-solid fa-spinner fa-spin text-stone-400 text-xl"></i>
                <span className="ml-3 text-stone-500 dark:text-stone-400">Loading users...</span>
              </div>
            ) : (
              /* Users Table */
              <div className="bg-white dark:bg-stone-900/50 border border-stone-200 dark:border-stone-800 rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
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
                                      <span className="ml-2 text-[10px] px-1.5 py-0.5 bg-sage-100 dark:bg-sage-900/30 text-sage-600 dark:text-sage-400 border border-sage-200 dark:border-sage-700 rounded font-medium">
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
                                {formatTimestamp(u.created_at)}
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
          <span className="text-[10px] text-stone-400 dark:text-stone-600 font-medium">
            Archetype User Management
          </span>
        </footer>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />

      {/* Create User Modal */}
      {activeModal === 'create' && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
            <div className="p-6 border-b border-stone-200 dark:border-stone-800">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
                  <i className="fa-solid fa-user-plus text-sage-600 dark:text-sage-400"></i>
                  Create User
                </h2>
                <button
                  onClick={resetModalState}
                  className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
                >
                  <i className="fa-solid fa-times text-lg"></i>
                </button>
              </div>
            </div>

            <form onSubmit={handleCreateUser}>
              <div className="p-6 space-y-4">
                {/* Username */}
                <div>
                  <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                    Username <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={createUsername}
                    onChange={(e) => setCreateUsername(e.target.value)}
                    className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white placeholder-stone-400 dark:placeholder-stone-500 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
                    placeholder="Enter username"
                    autoFocus
                    required
                  />
                </div>

                {/* Password */}
                <div>
                  <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                    Password <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="password"
                    value={createPassword}
                    onChange={(e) => setCreatePassword(e.target.value)}
                    className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white placeholder-stone-400 dark:placeholder-stone-500 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
                    placeholder="Enter password"
                    required
                  />
                  {/* Password strength indicator */}
                  {createPassword.length > 0 && (
                    <div className="mt-2">
                      <div className="h-1.5 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                        <div className={`h-full ${passwordStrength.color} ${passwordStrength.width} rounded-full transition-all duration-300`}></div>
                      </div>
                      <span className={`text-[10px] font-medium mt-1 block ${
                        passwordStrength.label === 'Weak' ? 'text-red-500' :
                        passwordStrength.label === 'Moderate' ? 'text-amber-500' :
                        'text-green-500'
                      }`}>
                        {passwordStrength.label}
                        {passwordStrength.label === 'Weak' && ' — use at least 10 characters'}
                        {passwordStrength.label === 'Moderate' && ' — use 15+ characters for strong'}
                      </span>
                    </div>
                  )}
                </div>

                {/* Email */}
                <div>
                  <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                    Email <span className="text-stone-400 dark:text-stone-500">(optional)</span>
                  </label>
                  <input
                    type="email"
                    value={createEmail}
                    onChange={(e) => setCreateEmail(e.target.value)}
                    className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white placeholder-stone-400 dark:placeholder-stone-500 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
                    placeholder="user@example.com"
                  />
                </div>

                {/* Role */}
                <div>
                  <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                    Role
                  </label>
                  <select
                    value={createRole}
                    onChange={(e) => setCreateRole(e.target.value as GlobalRole)}
                    className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
                  >
                    {GLOBAL_ROLES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label} — {r.description}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Modal error */}
                {modalError && (
                  <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
                    {modalError}
                  </div>
                )}
              </div>

              <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={resetModalState}
                  className="px-4 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={modalLoading || !createUsername.trim() || !createPassword.trim()}
                  className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                    !modalLoading && createUsername.trim() && createPassword.trim()
                      ? 'bg-sage-600 hover:bg-sage-700 text-white'
                      : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                  }`}
                >
                  {modalLoading ? (
                    <>
                      <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                      Creating...
                    </>
                  ) : (
                    <>
                      <i className="fa-solid fa-user-plus mr-2"></i>
                      Create User
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit User Modal */}
      {activeModal === 'edit' && selectedUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
            <div className="p-6 border-b border-stone-200 dark:border-stone-800">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
                  <i className="fa-solid fa-pen-to-square text-sage-600 dark:text-sage-400"></i>
                  Edit User
                </h2>
                <button
                  onClick={resetModalState}
                  className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
                >
                  <i className="fa-solid fa-times text-lg"></i>
                </button>
              </div>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                Editing <strong className="text-stone-700 dark:text-stone-300">{selectedUser.username}</strong>
              </p>
            </div>

            <form onSubmit={handleEditUser}>
              <div className="p-6 space-y-4">
                {/* Email */}
                <div>
                  <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                    Email
                  </label>
                  <input
                    type="email"
                    value={editEmail}
                    onChange={(e) => setEditEmail(e.target.value)}
                    className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white placeholder-stone-400 dark:placeholder-stone-500 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
                    placeholder="user@example.com"
                    autoFocus
                  />
                </div>

                {/* Role */}
                <div>
                  <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                    Role
                  </label>
                  <select
                    value={editRole}
                    onChange={(e) => setEditRole(e.target.value as GlobalRole)}
                    className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
                  >
                    {GLOBAL_ROLES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label} — {r.description}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Modal error */}
                {modalError && (
                  <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
                    {modalError}
                  </div>
                )}
              </div>

              <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={resetModalState}
                  className="px-4 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={modalLoading}
                  className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                    !modalLoading
                      ? 'bg-sage-600 hover:bg-sage-700 text-white'
                      : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                  }`}
                >
                  {modalLoading ? (
                    <>
                      <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                      Saving...
                    </>
                  ) : (
                    <>
                      <i className="fa-solid fa-check mr-2"></i>
                      Save Changes
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Reset Password Modal */}
      {activeModal === 'password' && selectedUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
            <div className="p-6 border-b border-stone-200 dark:border-stone-800">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
                  <i className="fa-solid fa-key text-amber-500"></i>
                  Reset Password
                </h2>
                <button
                  onClick={resetModalState}
                  className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
                >
                  <i className="fa-solid fa-times text-lg"></i>
                </button>
              </div>
              <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                Setting new password for <strong className="text-stone-700 dark:text-stone-300">{selectedUser.username}</strong>
              </p>
            </div>

            <form onSubmit={handleResetPassword}>
              <div className="p-6 space-y-4">
                {/* New Password */}
                <div>
                  <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                    New Password <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white placeholder-stone-400 dark:placeholder-stone-500 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
                    placeholder="Enter new password"
                    autoFocus
                    required
                  />
                  {/* Password strength indicator */}
                  {newPassword.length > 0 && (
                    <div className="mt-2">
                      <div className="h-1.5 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                        <div className={`h-full ${passwordStrength.color} ${passwordStrength.width} rounded-full transition-all duration-300`}></div>
                      </div>
                      <span className={`text-[10px] font-medium mt-1 block ${
                        passwordStrength.label === 'Weak' ? 'text-red-500' :
                        passwordStrength.label === 'Moderate' ? 'text-amber-500' :
                        'text-green-500'
                      }`}>
                        {passwordStrength.label}
                        {passwordStrength.label === 'Weak' && ' — use at least 10 characters'}
                        {passwordStrength.label === 'Moderate' && ' — use 15+ characters for strong'}
                      </span>
                    </div>
                  )}
                </div>

                {/* Modal error */}
                {modalError && (
                  <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
                    {modalError}
                  </div>
                )}
              </div>

              <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={resetModalState}
                  className="px-4 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={modalLoading || !newPassword.trim()}
                  className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                    !modalLoading && newPassword.trim()
                      ? 'bg-amber-600 hover:bg-amber-700 text-white'
                      : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                  }`}
                >
                  {modalLoading ? (
                    <>
                      <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                      Resetting...
                    </>
                  ) : (
                    <>
                      <i className="fa-solid fa-key mr-2"></i>
                      Reset Password
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
};

export default UserManagementPage;
