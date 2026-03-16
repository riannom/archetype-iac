import React, { useEffect, useState } from 'react';
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

  if (!user) return null;

  return (
    <Modal
      isOpen={isOpen}
      onClose={resetAndClose}
      title=""
      size="sm"
      showCloseButton={false}
    >
      <div className="flex items-center justify-between mb-1">
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
      <p className="text-xs text-stone-500 dark:text-stone-400 mb-4">
        Editing <strong className="text-stone-700 dark:text-stone-300">{user.username}</strong>
      </p>

      <form onSubmit={handleSubmit}>
        <div className="space-y-4">
          <Input
            type="email"
            label="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="user@example.com"
            autoFocus
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

          {/* Modal error */}
          {error && (
            <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
              {error}
            </div>
          )}
        </div>

        <ModalFooter>
          <Button variant="secondary" type="button" onClick={resetAndClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            disabled={loading}
            loading={loading}
            leftIcon="fa-solid fa-check"
          >
            {loading ? 'Saving...' : 'Save Changes'}
          </Button>
        </ModalFooter>
      </form>
    </Modal>
  );
};

export default UserEditModal;
