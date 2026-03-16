import React, { useState } from 'react';
import { type User } from '../contexts/UserContext';
import { apiRequest } from '../api';
import { Modal, ModalFooter } from '../components/ui/Modal';
import { Input } from '../components/ui/Input';
import { Button } from '../components/ui/Button';

function getPasswordStrength(password: string): { label: string; color: string; width: string } {
  if (password.length === 0) return { label: '', color: '', width: 'w-0' };
  if (password.length < 10) return { label: 'Weak', color: 'bg-red-500', width: 'w-1/3' };
  if (password.length < 15) return { label: 'Moderate', color: 'bg-amber-500', width: 'w-2/3' };
  return { label: 'Strong', color: 'bg-green-500', width: 'w-full' };
}

interface UserPasswordModalProps {
  isOpen: boolean;
  user: User | null;
  onClose: () => void;
  onReset: () => void;
}

const UserPasswordModal: React.FC<UserPasswordModalProps> = ({ isOpen, user, onClose, onReset }) => {
  const [newPassword, setNewPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const resetAndClose = () => {
    setNewPassword('');
    setError(null);
    setLoading(false);
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user || !newPassword.trim()) {
      setError('Password is required.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await apiRequest<{ status: string }>(`/users/${user.id}/password`, {
        method: 'PUT',
        body: JSON.stringify({ new_password: newPassword }),
      });
      resetAndClose();
      onReset();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset password');
    } finally {
      setLoading(false);
    }
  };

  if (!user) return null;

  const passwordStrength = getPasswordStrength(newPassword);

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
          <i className="fa-solid fa-key text-amber-500"></i>
          Reset Password
        </h2>
        <button
          onClick={resetAndClose}
          className="text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
        >
          <i className="fa-solid fa-times text-lg"></i>
        </button>
      </div>
      <p className="text-xs text-stone-500 dark:text-stone-400 mb-4">
        Setting new password for <strong className="text-stone-700 dark:text-stone-300">{user.username}</strong>
      </p>

      <form onSubmit={handleSubmit}>
        <div className="space-y-4">
          <Input
            type="password"
            label="New Password *"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="Enter new password"
            autoFocus
            required
          />
          {/* Password strength indicator */}
          {newPassword.length > 0 && (
            <div>
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
            type="submit"
            disabled={loading || !newPassword.trim()}
            loading={loading}
            leftIcon="fa-solid fa-key"
            className={`${
              !loading && newPassword.trim()
                ? 'bg-amber-600 hover:bg-amber-700 text-white border-amber-600 hover:border-amber-700'
                : ''
            }`}
          >
            {loading ? 'Resetting...' : 'Reset Password'}
          </Button>
        </ModalFooter>
      </form>
    </Modal>
  );
};

export default UserPasswordModal;
