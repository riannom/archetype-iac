import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import UserCreateModal from './UserCreateModal';

const apiRequestMock = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequestMock(...args),
}));

describe('UserCreateModal', () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  function renderModal(overrides: Partial<React.ComponentProps<typeof UserCreateModal>> = {}) {
    const props = {
      isOpen: true,
      onClose: vi.fn(),
      onCreated: vi.fn(),
      ...overrides,
    };
    return { ...render(<UserCreateModal {...props} />), props };
  }

  it('does not render content when closed', () => {
    renderModal({ isOpen: false });
    expect(screen.queryByText('Create User')).not.toBeInTheDocument();
  });

  it('renders inputs and disables submit until username and password are set', () => {
    renderModal();
    const submit = screen.getByRole('button', { name: /Create User/i });
    expect(submit).toBeDisabled();
  });

  it('hides the password strength meter when the field is empty', () => {
    renderModal();
    expect(screen.queryByText(/use at least 10 characters/)).not.toBeInTheDocument();
    expect(screen.queryByText('Weak')).not.toBeInTheDocument();
  });

  it('classifies password strength as Weak/Moderate/Strong by length', async () => {
    const user = userEvent.setup();
    renderModal();
    const passwordInput = screen.getByPlaceholderText('Enter password');

    await user.type(passwordInput, 'short');
    expect(screen.getByText(/Weak/)).toBeInTheDocument();

    await user.clear(passwordInput);
    await user.type(passwordInput, 'twelve-chars');
    expect(screen.getByText(/Moderate/)).toBeInTheDocument();

    await user.clear(passwordInput);
    await user.type(passwordInput, 'a-very-long-strong-password');
    expect(screen.getByText('Strong')).toBeInTheDocument();
  });

  it('disables submit when fields are blank after trim', async () => {
    const user = userEvent.setup();
    renderModal();
    const submit = screen.getByRole('button', { name: /Create User/i });

    await user.type(screen.getByPlaceholderText('Enter username'), '   ');
    await user.type(screen.getByPlaceholderText('Enter password'), '   ');

    expect(submit).toBeDisabled();
  });

  it('shows the inline required-fields error when the form is submitted with whitespace values', () => {
    // The submit button is disabled in this state (covered above), so the
    // browser path is unreachable in normal UI use. Fire the form-submit
    // event directly to exercise the handleSubmit guard.
    const { container } = renderModal();
    const usernameInput = screen.getByPlaceholderText('Enter username') as HTMLInputElement;
    const passwordInput = screen.getByPlaceholderText('Enter password') as HTMLInputElement;
    fireEvent.change(usernameInput, { target: { value: '   ' } });
    fireEvent.change(passwordInput, { target: { value: '   ' } });

    const form = container.querySelector('form');
    expect(form).not.toBeNull();
    fireEvent.submit(form!);

    expect(screen.getByText(/Username and password are required/)).toBeInTheDocument();
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it('submits trimmed username/password with selected role and skips email when blank', async () => {
    apiRequestMock.mockResolvedValue({ id: 'u1' });
    const user = userEvent.setup();
    const { props } = renderModal();

    await user.type(screen.getByPlaceholderText('Enter username'), '  alice  ');
    await user.type(screen.getByPlaceholderText('Enter password'), 'pw123456789');
    await user.selectOptions(screen.getByLabelText(/Role/i), 'admin');
    await user.click(screen.getByRole('button', { name: /Create User/i }));

    await waitFor(() => expect(apiRequestMock).toHaveBeenCalledTimes(1));
    expect(apiRequestMock).toHaveBeenCalledWith(
      '/users',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          username: 'alice',
          password: 'pw123456789',
          global_role: 'admin',
        }),
      })
    );
    expect(props.onCreated).toHaveBeenCalledTimes(1);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('includes the email in the payload when provided', async () => {
    apiRequestMock.mockResolvedValue({ id: 'u1' });
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByPlaceholderText('Enter username'), 'bob');
    await user.type(screen.getByPlaceholderText('Enter password'), 'pw1234');
    await user.type(screen.getByPlaceholderText('user@example.com'), '  bob@x.io  ');
    await user.click(screen.getByRole('button', { name: /Create User/i }));

    await waitFor(() => expect(apiRequestMock).toHaveBeenCalled());
    const body = JSON.parse(apiRequestMock.mock.calls[0][1].body);
    expect(body.email).toBe('bob@x.io');
  });

  it('surfaces an Error message from a failed apiRequest', async () => {
    apiRequestMock.mockRejectedValue(new Error('username already exists'));
    const user = userEvent.setup();
    const { props } = renderModal();

    await user.type(screen.getByPlaceholderText('Enter username'), 'alice');
    await user.type(screen.getByPlaceholderText('Enter password'), 'pw1234');
    await user.click(screen.getByRole('button', { name: /Create User/i }));

    expect(await screen.findByText(/username already exists/)).toBeInTheDocument();
    expect(props.onCreated).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it('falls back to a generic message when the rejection is not an Error', async () => {
    apiRequestMock.mockRejectedValue('boom');
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByPlaceholderText('Enter username'), 'alice');
    await user.type(screen.getByPlaceholderText('Enter password'), 'pw1234');
    await user.click(screen.getByRole('button', { name: /Create User/i }));

    expect(await screen.findByText(/Failed to create user/)).toBeInTheDocument();
  });

  it('cancel resets the form and calls onClose without onCreated', async () => {
    const user = userEvent.setup();
    const { props } = renderModal();

    await user.type(screen.getByPlaceholderText('Enter username'), 'alice');
    await user.click(screen.getByRole('button', { name: /Cancel/i }));

    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(props.onCreated).not.toHaveBeenCalled();
    expect(apiRequestMock).not.toHaveBeenCalled();
  });
});
