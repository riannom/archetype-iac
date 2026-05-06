import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import UserPasswordModal from './UserPasswordModal';
import type { User } from '../contexts/UserContext';

const apiRequestMock = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequestMock(...args),
}));

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 'u1',
    username: 'alice',
    email: 'alice@example.com',
    global_role: 'viewer',
    created_at: '2026-01-01T00:00:00Z',
    last_login_at: null,
    ...overrides,
  } as User;
}

describe('UserPasswordModal', () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  function renderModal(overrides: Partial<React.ComponentProps<typeof UserPasswordModal>> = {}) {
    const props = {
      isOpen: true,
      user: makeUser(),
      onClose: vi.fn(),
      onReset: vi.fn(),
      ...overrides,
    };
    return { ...render(<UserPasswordModal {...props} />), props };
  }

  it('renders nothing when user is null', () => {
    const { container } = renderModal({ user: null });
    expect(container.textContent).toBe('');
  });

  it('shows the target username in the subheading', () => {
    renderModal();
    expect(screen.getByText(/Setting new password/)).toHaveTextContent('alice');
  });

  it('disables submit until a non-blank password is entered', async () => {
    const user = userEvent.setup();
    renderModal();
    const submit = screen.getByRole('button', { name: /Reset Password/i });
    expect(submit).toBeDisabled();

    await user.type(screen.getByPlaceholderText('Enter new password'), 'pw');
    expect(submit).toBeEnabled();
  });

  it('classifies password strength tiers', async () => {
    const user = userEvent.setup();
    renderModal();
    const input = screen.getByPlaceholderText('Enter new password');

    await user.type(input, 'short');
    expect(screen.getByText(/Weak/)).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, 'twelve-chars');
    expect(screen.getByText(/Moderate/)).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, 'a-very-long-strong-pass');
    expect(screen.getByText('Strong')).toBeInTheDocument();
  });

  it('shows the inline required error on whitespace-only submit', () => {
    const { container } = renderModal();
    const passwordInput = screen.getByPlaceholderText('Enter new password') as HTMLInputElement;
    fireEvent.change(passwordInput, { target: { value: '   ' } });

    const form = container.querySelector('form')!;
    fireEvent.submit(form);

    expect(screen.getByText(/Password is required/)).toBeInTheDocument();
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it('PUTs /users/{id}/password with the entered password', async () => {
    apiRequestMock.mockResolvedValue({ status: 'ok' });
    const user = userEvent.setup();
    const { props } = renderModal();

    await user.type(screen.getByPlaceholderText('Enter new password'), 'super-secret');
    await user.click(screen.getByRole('button', { name: /Reset Password/i }));

    await waitFor(() => expect(apiRequestMock).toHaveBeenCalledTimes(1));
    expect(apiRequestMock).toHaveBeenCalledWith(
      '/users/u1/password',
      expect.objectContaining({ method: 'PUT' })
    );
    const body = JSON.parse(apiRequestMock.mock.calls[0][1].body);
    expect(body).toEqual({ new_password: 'super-secret' });
    expect(props.onReset).toHaveBeenCalledTimes(1);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('surfaces an Error from a failed reset', async () => {
    apiRequestMock.mockRejectedValue(new Error('weak password'));
    const user = userEvent.setup();
    const { props } = renderModal();

    await user.type(screen.getByPlaceholderText('Enter new password'), 'short');
    await user.click(screen.getByRole('button', { name: /Reset Password/i }));

    expect(await screen.findByText(/weak password/)).toBeInTheDocument();
    expect(props.onReset).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it('falls back to a generic message when the rejection is not an Error', async () => {
    apiRequestMock.mockRejectedValue('boom');
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByPlaceholderText('Enter new password'), 'pw');
    await user.click(screen.getByRole('button', { name: /Reset Password/i }));

    expect(await screen.findByText(/Failed to reset password/)).toBeInTheDocument();
  });

  it('cancel calls onClose without onReset', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();
    await user.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(props.onReset).not.toHaveBeenCalled();
  });
});
