import React, { useState } from 'react';
import { type GlobalRole, type User } from '../contexts/UserContext';
import { apiRequest } from '../api';

const GLOBAL_ROLES: { value: GlobalRole; label: string; description: string }[] = [
  { value: 'super_admin', label: 'Super Admin', description: 'Full system access' },
  { value: 'admin', label: 'Admin', description: 'Manage users and labs' },
  { value: 'operator', label: 'Operator', description: 'Deploy and manage labs' },
  { value: 'viewer', label: 'Viewer', description: 'Read-only access' },
];

function getPasswordStrength(password: string): { label: string; color: string; width: string } {
  if (password.length === 0) return { label: '', color: '', width: 'w-0' };
  if (password.length < 10) return { label: 'Weak', color: 'bg-red-500', width: 'w-1/3' };
  if (password.length < 15) return { label: 'Moderate', color: 'bg-amber-500', width: 'w-2/3' };
  return { label: 'Strong', color: 'bg-green-500', width: 'w-full' };
}

interface CreateUserPayload {
  username: string;
  password: string;
  email?: string;
  global_role?: GlobalRole;
}

interface UserCreateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: () => void;
}

const UserCreateModal: React.FC<UserCreateModalProps> = ({ isOpen, onClose, onCreated }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<GlobalRole>('viewer');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const resetAndClose = () => {
    setUsername('');
    setPassword('');
    setEmail('');
    setRole('viewer');
    setError(null);
    setLoading(false);
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      setError('Username and password are required.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const payload: CreateUserPayload = {
        username: username.trim(),
        password,
        global_role: role,
      };
      if (email.trim()) {
        payload.email = email.trim();
      }
      await apiRequest<User>('/users', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      resetAndClose();
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create user');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  const passwordStrength = getPasswordStrength(password);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-stone-900 rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="p-6 border-b border-stone-200 dark:border-stone-800">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
              <i className="fa-solid fa-user-plus text-sage-600 dark:text-sage-400"></i>
              Create User
            </h2>
            <button
              onClick={resetAndClose}
              className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
            >
              <i className="fa-solid fa-times text-lg"></i>
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="p-6 space-y-4">
            {/* Username */}
            <div>
              <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1.5">
                Username <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
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
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-3 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white placeholder-stone-400 dark:placeholder-stone-500 focus:outline-none focus:ring-2 focus:ring-sage-500/50 focus:border-sage-500"
                placeholder="Enter password"
                required
              />
              {/* Password strength indicator */}
              {password.length > 0 && (
                <div className="mt-2">
                  <div className="h-1.5 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                    <div className={`h-full ${passwordStrength.color} ${passwordStrength.width} rounded-full transition-all duration-300`}></div>
                  </div>
                  <span className={`text-[11px] font-medium mt-1 block ${
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
                value={email}
                onChange={(e) => setEmail(e.target.value)}
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
              disabled={loading || !username.trim() || !password.trim()}
              className={`px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                !loading && username.trim() && password.trim()
                  ? 'bg-sage-600 hover:bg-sage-700 text-white'
                  : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
              }`}
            >
              {loading ? (
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
  );
};

export default UserCreateModal;
