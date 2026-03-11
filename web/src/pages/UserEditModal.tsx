import React, { useEffect, useState } from 'react';
import { type GlobalRole, type User } from '../contexts/UserContext';
import { apiRequest } from '../api';

const GLOBAL_ROLES: { value: GlobalRole; label: string; description: string }[] = [
  { value: 'super_admin', label: 'Super Admin', description: 'Full system access' },
  { value: 'admin', label: 'Admin', description: 'Manage users and labs' },
  { value: 'operator', label: 'Operator', description: 'Deploy and manage labs' },
  { value: 'viewer', label: 'Viewer', description: 'Read-only access' },
];

interface EditUserPayload {
  email?: string;
  global_role?: GlobalRole;
}

interface UserEditModalProps {
  isOpen: boolean;
  user: User | null;
  onClose: () => void;
  onSaved: () => void;
}

const UserEditModal: React.FC<UserEditModalProps> = ({ isOpen, user, onClose, onSaved }) => {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<GlobalRole>('viewer');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isOpen && user) {
      setEmail(user.email || '');
      setRole(user.global_role);
      setError(null);
      setLoading(false);
    }
  }, [isOpen, user]);

  const resetAndClose = () => {
    setEmail('');
    setRole('viewer');
    setError(null);
    setLoading(false);
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    setLoading(true);
    setError(null);
    try {
      const payload: EditUserPayload = {
        email: email.trim() || undefined,
        global_role: role,
      };
      await apiRequest<User>(`/users/${user.id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      });
      resetAndClose();
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update user');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen || !user) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="p-6 border-b border-stone-200 dark:border-stone-800">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
              <i className="fa-solid fa-pen-to-square text-sage-600 dark:text-sage-400"></i>
              Edit User
            </h2>
            <button
              onClick={resetAndClose}
              className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
            >
              <i className="fa-solid fa-times text-lg"></i>
            </button>
          </div>
          <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
            Editing <strong className="text-stone-700 dark:text-stone-300">{user.username}</strong>
          </p>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="p-6 space-y-4">
            {/* Email */}
            <div>
              <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
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
                value={role}
                onChange={(e) => setRole(e.target.value as GlobalRole)}
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
            {error && (
              <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
                {error}
              </div>
            )}
          </div>

          <div className="p-6 border-t border-stone-200 dark:border-stone-800 flex justify-end gap-3">
            <button
              type="button"
              onClick={resetAndClose}
              className="px-4 py-2 glass-control text-stone-600 dark:text-stone-400 rounded-lg transition-all text-sm font-medium"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                !loading
                  ? 'bg-sage-600 hover:bg-sage-700 text-white'
                  : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
              }`}
            >
              {loading ? (
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
  );
};

export default UserEditModal;
