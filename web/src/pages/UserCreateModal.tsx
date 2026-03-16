import React, { useState } from 'react';
import { type GlobalRole, type User } from '../contexts/UserContext';
import { apiRequest } from '../api';
import { Modal, ModalFooter } from '../components/ui/Modal';
import { Input } from '../components/ui/Input';
import { Select } from '../components/ui/Select';
import { Button } from '../components/ui/Button';

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

  const passwordStrength = getPasswordStrength(password);

  return (
    <Modal isOpen={isOpen} onClose={resetAndClose} title="Create User" size="sm">
      <form onSubmit={handleSubmit}>
        <div className="space-y-4">
          <Input
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Enter username"
            leftIcon="fa-solid fa-user"
            autoFocus
            required
          />

          <div>
            <Input
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              leftIcon="fa-solid fa-lock"
              required
            />
            {password.length > 0 && (
              <div className="mt-2">
                <div className="h-1.5 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
                  <div className={`h-full ${passwordStrength.color} ${passwordStrength.width} rounded-full transition-all duration-300`} />
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

          <Input
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="user@example.com"
            leftIcon="fa-solid fa-envelope"
            hint="Optional"
          />

          <Select
            label="Role"
            value={role}
            onChange={(e) => setRole(e.target.value as GlobalRole)}
            options={GLOBAL_ROLES.map((r) => ({
              value: r.value,
              label: `${r.label} — ${r.description}`,
            }))}
          />

          {error && (
            <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300 flex items-center gap-2">
              <i className="fa-solid fa-circle-xmark text-red-500 flex-shrink-0" />
              {error}
            </div>
          )}
        </div>

        <ModalFooter>
          <Button type="button" variant="ghost" onClick={resetAndClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={loading}
            disabled={!username.trim() || !password.trim()}
            leftIcon="fa-solid fa-user-plus"
          >
            Create User
          </Button>
        </ModalFooter>
      </form>
    </Modal>
  );
};

export default UserCreateModal;
