import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import UserEditModal from './UserEditModal';
import type { GlobalRole, User } from '../contexts/UserContext';

const apiRequestMock = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequestMock(...args),
}));

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 'u1',
    username: 'alice',
    email: 'alice@example.com',
    global_role: 'operator' as GlobalRole,
    created_at: '2026-01-01T00:00:00Z',
    last_login_at: null,
    ...overrides,
  } as User;
}

describe('UserEditModal', () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  function renderModal(overrides: Partial<React.ComponentProps<typeof UserEditModal>> = {}) {
    const props = {
      isOpen: true,
      user: makeUser(),
      onClose: vi.fn(),
      onSaved: vi.fn(),
      ...overrides,
    };
    return { ...render(<UserEditModal {...props} />), props };
  }

  it('renders nothing when user is null', () => {
    const { container } = renderModal({ user: null });
    expect(container.textContent).toBe('');
  });

  it('seeds the form fields from the supplied user', () => {
    renderModal();
    expect(screen.getByDisplayValue('alice@example.com')).toBeInTheDocument();
    expect(screen.getByText(/Editing/)).toHaveTextContent('alice');
  });

  it('treats a missing email as empty rather than undefined', () => {
    renderModal({ user: makeUser({ email: undefined as unknown as string }) });
    const emailInput = screen.getByPlaceholderText('user@example.com') as HTMLInputElement;
    expect(emailInput.value).toBe('');
  });

  it('PATCHes /users/{id} with trimmed email and selected role on save', async () => {
    apiRequestMock.mockResolvedValue({ id: 'u1' });
    const user = userEvent.setup();
    const { props } = renderModal();

    const emailInput = screen.getByPlaceholderText('user@example.com');
    await user.clear(emailInput);
    await user.type(emailInput, '  new@x.io  ');
    await user.selectOptions(screen.getByLabelText(/Role/i), 'admin');
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => expect(apiRequestMock).toHaveBeenCalledTimes(1));
    expect(apiRequestMock).toHaveBeenCalledWith(
      '/users/u1',
      expect.objectContaining({ method: 'PATCH' })
    );
    const body = JSON.parse(apiRequestMock.mock.calls[0][1].body);
    expect(body).toEqual({ email: 'new@x.io', global_role: 'admin' });
    expect(props.onSaved).toHaveBeenCalledTimes(1);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('omits email when blank (sends undefined)', async () => {
    apiRequestMock.mockResolvedValue({ id: 'u1' });
    const user = userEvent.setup();
    renderModal();

    const emailInput = screen.getByPlaceholderText('user@example.com');
    await user.clear(emailInput);
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => expect(apiRequestMock).toHaveBeenCalled());
    const body = JSON.parse(apiRequestMock.mock.calls[0][1].body);
    expect(body.email).toBeUndefined();
    expect(body.global_role).toBe('operator');
  });

  it('surfaces an Error message on PATCH failure', async () => {
    apiRequestMock.mockRejectedValue(new Error('forbidden'));
    const user = userEvent.setup();
    const { props } = renderModal();

    await user.click(screen.getByRole('button', { name: /Save Changes/i }));
    expect(await screen.findByText(/forbidden/)).toBeInTheDocument();
    expect(props.onSaved).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it('falls back to "Failed to update user" when the rejection is not an Error', async () => {
    apiRequestMock.mockRejectedValue('boom');
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole('button', { name: /Save Changes/i }));
    expect(await screen.findByText(/Failed to update user/)).toBeInTheDocument();
  });

  it('cancel button calls onClose without onSaved', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();
    await user.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(props.onSaved).not.toHaveBeenCalled();
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it('reseeds form fields when the user prop changes after open', async () => {
    const { rerender } = renderModal({ user: makeUser({ email: 'first@x.io' }) });
    expect(screen.getByDisplayValue('first@x.io')).toBeInTheDocument();

    rerender(
      <UserEditModal
        isOpen
        user={makeUser({ id: 'u2', username: 'bob', email: 'second@x.io' })}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByDisplayValue('second@x.io')).toBeInTheDocument();
    });
    expect(screen.getByText(/Editing/)).toHaveTextContent('bob');
  });
});
