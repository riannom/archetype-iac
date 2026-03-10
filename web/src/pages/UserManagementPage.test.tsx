import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import UserManagementPage from './UserManagementPage';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockToggleMode = vi.fn();

vi.mock('../theme/index', () => ({
  useTheme: () => ({ effectiveMode: 'light', toggleMode: mockToggleMode }),
  ThemeSelector: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="theme-selector">ThemeSelector</div> : null,
}));

let mockUser: Record<string, unknown> | null = {
  id: 'u1',
  username: 'admin',
  global_role: 'super_admin',
  is_active: true,
  email: 'admin@example.com',
  created_at: '2024-01-01T00:00:00Z',
};
let mockUserLoading = false;

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({
    user: mockUser,
    loading: mockUserLoading,
  }),
}));

let mockCanManageUsers = true;

vi.mock('../utils/permissions', () => ({
  canManageUsers: () => mockCanManageUsers,
}));

vi.mock('../api', () => ({
  apiRequest: vi.fn(),
}));

vi.mock('../components/AdminMenuButton', () => ({
  default: () => <div data-testid="admin-menu">AdminMenuButton</div>,
}));

const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    Navigate: ({ to }: { to: string }) => (
      <div data-testid="redirect">Redirect {to}</div>
    ),
  };
});

const { apiRequest } = await import('../api');

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const sampleUsers = [
  {
    id: 'u1',
    username: 'admin',
    email: 'admin@example.com',
    global_role: 'super_admin',
    is_active: true,
    created_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 'u2',
    username: 'bob',
    email: 'bob@example.com',
    global_role: 'operator',
    is_active: true,
    created_at: '2024-06-15T00:00:00Z',
  },
  {
    id: 'u3',
    username: 'carol',
    email: '',
    global_role: 'viewer',
    is_active: false,
    created_at: '2024-09-01T00:00:00Z',
  },
];

function loadUsersOk(users = sampleUsers) {
  (apiRequest as any).mockResolvedValueOnce({
    users,
    total: users.length,
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('UserManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = {
      id: 'u1',
      username: 'admin',
      global_role: 'super_admin',
      is_active: true,
      email: 'admin@example.com',
      created_at: '2024-01-01T00:00:00Z',
    };
    mockUserLoading = false;
    mockCanManageUsers = true;
  });

  // ========================================================================
  // Basic rendering
  // ========================================================================

  it('loads users and renders table', async () => {
    loadUsersOk();

    render(<UserManagementPage />);

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/users'));
    expect(screen.getByText('admin')).toBeInTheDocument();
  });

  it('shows all users in the table', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => {
      expect(screen.getByText('admin')).toBeInTheDocument();
      expect(screen.getByText('bob')).toBeInTheDocument();
    });
  });

  it('renders role badges for all user types', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => {
      expect(screen.getByText('admin')).toBeInTheDocument();
    });

    expect(screen.getByText('Operator')).toBeInTheDocument();
    expect(screen.getByText('Viewer')).toBeInTheDocument();
    expect(screen.getByText('Super Admin')).toBeInTheDocument();
  });

  it('shows the Create User button for admin users', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => {
      const createBtn = screen.getAllByRole('button', { name: 'Create User' });
      expect(createBtn.length).toBeGreaterThan(0);
    });
  });

  // ========================================================================
  // Loading & error states
  // ========================================================================

  it('shows loading spinner while users are being fetched', async () => {
    (apiRequest as any).mockReturnValue(new Promise(() => {}));

    render(<UserManagementPage />);

    expect(screen.getByText('Loading users...')).toBeInTheDocument();
  });

  it('displays error banner when user list fails to load', async () => {
    (apiRequest as any).mockRejectedValueOnce(new Error('Network error'));

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });
  });

  it('dismisses the error banner when close button is clicked', async () => {
    (apiRequest as any).mockRejectedValueOnce(new Error('Server down'));

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText('Server down')).toBeInTheDocument();
    });

    const errorBanner = screen.getByText('Server down').closest('div')!;
    const dismissBtn = errorBanner.querySelector('button')!;
    fireEvent.click(dismissBtn);

    await waitFor(() => {
      expect(screen.queryByText('Server down')).not.toBeInTheDocument();
    });
  });

  // ========================================================================
  // Empty state
  // ========================================================================

  it('shows empty state message when no users exist', async () => {
    (apiRequest as any).mockResolvedValueOnce({ users: [], total: 0 });

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText('No users found.')).toBeInTheDocument();
    });
  });

  it('shows correct user count in subtitle', async () => {
    loadUsersOk();

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText('3 users registered')).toBeInTheDocument();
    });
  });

  it('shows singular form for one user', async () => {
    const oneUser = [sampleUsers[0]];
    (apiRequest as any).mockResolvedValueOnce({ users: oneUser, total: 1 });

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText('1 user registered')).toBeInTheDocument();
    });
  });

  it('handles API response with missing users array gracefully', async () => {
    (apiRequest as any).mockResolvedValueOnce({ total: 5 });

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText(/No users found/)).toBeInTheDocument();
    });
  });

  // ========================================================================
  // Current-user indicators
  // ========================================================================

  it('marks the current user row with "You" badge', async () => {
    loadUsersOk();

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText('You')).toBeInTheDocument();
    });
  });

  it('does not show toggle-active button for the current user (self)', async () => {
    loadUsersOk();

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText('admin')).toBeInTheDocument();
    });

    const deactivateBtns = screen.queryAllByTitle('Deactivate user');
    const activateBtns = screen.queryAllByTitle('Activate user');
    const allToggle = [...deactivateBtns, ...activateBtns];
    expect(allToggle).toHaveLength(2);
  });

  // ========================================================================
  // Permissions
  // ========================================================================

  it('redirects non-admin users', async () => {
    mockCanManageUsers = false;

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByTestId('redirect')).toBeInTheDocument();
    });
  });

  // ========================================================================
  // Create user
  // ========================================================================

  it('shows create modal and validates empty fields', async () => {
    (apiRequest as any).mockResolvedValue({ users: [], total: 0 });

    render(<UserManagementPage />);

    fireEvent.click(await screen.findAllByRole('button', { name: 'Create User' }).then((buttons) => buttons[0]));
    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    const submitButton = createButtons[createButtons.length - 1] as HTMLButtonElement;
    expect(submitButton.disabled).toBe(true);
  });

  it('creates a user with valid username and password', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length })
      .mockResolvedValueOnce({ id: 'u5', username: 'newuser', global_role: 'viewer' })
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    fireEvent.change(screen.getByPlaceholderText('Enter username'), { target: { value: 'newuser' } });
    fireEvent.change(screen.getByPlaceholderText('Enter password'), { target: { value: 'StrongPass123!' } });

    const submitButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/users', expect.objectContaining({ method: 'POST' }));
    });
  });

  it('creates a user with email field populated', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length })
      .mockResolvedValueOnce({ id: 'u5', username: 'emailuser', email: 'test@test.com' })
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    fireEvent.change(screen.getByPlaceholderText('Enter username'), { target: { value: 'emailuser' } });
    fireEvent.change(screen.getByPlaceholderText('Enter password'), { target: { value: 'password123' } });

    const emailInput = screen.getByPlaceholderText('user@example.com');
    fireEvent.change(emailInput, { target: { value: 'test@test.com' } });

    const submitButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/users', expect.objectContaining({ method: 'POST' }));
    });
  });

  it('creates user with specific role selection', async () => {
    loadUsersOk();
    (apiRequest as any)
      .mockResolvedValueOnce({ id: 'u5', username: 'newop' })
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createBtns = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createBtns[0]);

    fireEvent.change(screen.getByPlaceholderText('Enter username'), { target: { value: 'newop' } });
    fireEvent.change(screen.getByPlaceholderText('Enter password'), { target: { value: 'operatorpass123' } });

    const roleSelect = screen.getByDisplayValue(/Viewer/);
    fireEvent.change(roleSelect, { target: { value: 'operator' } });

    const submitBtns = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(submitBtns[submitBtns.length - 1]);

    await waitFor(() => {
      const call = (apiRequest as any).mock.calls.find(
        (c: any[]) => c[0] === '/users' && c[1]?.method === 'POST'
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(call[1].body);
      expect(body.global_role).toBe('operator');
      expect(body.username).toBe('newop');
    });
  });

  it('shows modal error when create user API fails', async () => {
    loadUsersOk();
    (apiRequest as any).mockRejectedValueOnce(new Error('Username already exists'));

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createBtns = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createBtns[0]);

    fireEvent.change(screen.getByPlaceholderText('Enter username'), { target: { value: 'duplicate' } });
    fireEvent.change(screen.getByPlaceholderText('Enter password'), { target: { value: 'SomePassword123' } });

    const submitBtns = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(submitBtns[submitBtns.length - 1]);

    await waitFor(() => {
      expect(screen.getByText('Username already exists')).toBeInTheDocument();
    });
  });

  // ========================================================================
  // Password strength
  // ========================================================================

  it('shows password strength meter for weak password', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    const passwordInput = screen.getByPlaceholderText('Enter password');
    fireEvent.change(passwordInput, { target: { value: 'short' } });

    await waitFor(() => {
      expect(screen.getByText(/Weak/)).toBeInTheDocument();
    });
  });

  it('shows Strong password strength for long password', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    const passwordInput = screen.getByPlaceholderText('Enter password');
    fireEvent.change(passwordInput, { target: { value: 'ThisIsAVeryStrongPassword123!' } });

    await waitFor(() => {
      expect(screen.getByText('Strong')).toBeInTheDocument();
    });
  });

  it('shows Moderate password strength for medium-length password', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    const passwordInput = screen.getByPlaceholderText('Enter password');
    fireEvent.change(passwordInput, { target: { value: '12DigitPass!' } });

    await waitFor(() => {
      expect(screen.getByText(/Moderate/)).toBeInTheDocument();
    });
  });

  // ========================================================================
  // Toggle active/inactive
  // ========================================================================

  it('toggles a user active/inactive via toggle button', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length })
      .mockResolvedValueOnce({ status: 'ok' })
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('bob')).toBeInTheDocument());

    const toggleBtns = screen.queryAllByTitle(/activate user|deactivate user/i);
    expect(toggleBtns.length).toBeGreaterThan(0);

    fireEvent.click(toggleBtns[0]);
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        expect.stringMatching(/\/users\/u\d+\/(activate|deactivate)/),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  it('shows error banner when toggle active API fails', async () => {
    loadUsersOk();
    (apiRequest as any).mockRejectedValueOnce(new Error('Cannot deactivate'));

    render(<UserManagementPage />);
    await waitFor(() =>
      expect(screen.getAllByText('bob').length).toBeGreaterThanOrEqual(1)
    );

    const deactivateBtns = screen.getAllByTitle('Deactivate user');
    fireEvent.click(deactivateBtns[0]);

    await waitFor(() => {
      expect(screen.getByText(/Cannot deactivate/)).toBeInTheDocument();
    });
  });

  // ========================================================================
  // Edit modal
  // ========================================================================

  it('opens edit modal via pen icon button', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('bob')).toBeInTheDocument());

    const editBtns = screen.getAllByTitle('Edit user');
    fireEvent.click(editBtns[0]);
    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });
  });

  it('cancels edit modal without saving', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const editBtns = screen.getAllByTitle('Edit user');
    fireEvent.click(editBtns[0]);
    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });

    const cancelBtn = screen.getByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(screen.queryByText('Edit User')).not.toBeInTheDocument();
    });
  });

  it('submits edit user form', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length })
      .mockResolvedValueOnce({ id: 'u2', username: 'bob', global_role: 'admin' })
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('bob')).toBeInTheDocument());

    const editBtns = screen.getAllByTitle('Edit user');
    fireEvent.click(editBtns[1]);

    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });

    const saveBtn = screen.getByRole('button', { name: /save changes/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        expect.stringMatching(/\/users\/u\d+/),
        expect.objectContaining({ method: 'PATCH' })
      );
    });
  });

  it('shows modal error when edit user API fails', async () => {
    loadUsersOk();
    (apiRequest as any).mockRejectedValueOnce(new Error('Permission denied'));

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('bob')).toBeInTheDocument());

    const editBtns = screen.getAllByTitle('Edit user');
    fireEvent.click(editBtns[1]);

    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });

    const saveBtn = screen.getByRole('button', { name: /save changes/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText('Permission denied')).toBeInTheDocument();
    });
  });

  it('pre-populates edit modal with selected user email and role', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('bob')).toBeInTheDocument());

    const editBtns = screen.getAllByTitle('Edit user');
    fireEvent.click(editBtns[1]);

    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });

    const emailInput = screen.getByPlaceholderText('user@example.com') as HTMLInputElement;
    expect(emailInput.value).toBe('bob@example.com');
  });

  // ========================================================================
  // Password reset
  // ========================================================================

  it('opens password reset modal via key icon button', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const resetBtns = screen.getAllByTitle('Reset password');
    fireEvent.click(resetBtns[0]);
    await waitFor(() => {
      const heading = screen.getByRole('heading', { name: /reset password/i });
      expect(heading).toBeInTheDocument();
    });
  });

  it('submits password reset successfully', async () => {
    loadUsersOk();
    (apiRequest as any).mockResolvedValueOnce({ status: 'ok' });

    render(<UserManagementPage />);
    await waitFor(() =>
      expect(screen.getAllByText('bob').length).toBeGreaterThanOrEqual(1)
    );

    const resetBtns = screen.getAllByTitle('Reset password');
    fireEvent.click(resetBtns[1]);

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /reset password/i })
      ).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText('Enter new password'), {
      target: { value: 'NewSecurePassword!' },
    });

    const modalForm = screen.getByPlaceholderText('Enter new password').closest('form')!;
    fireEvent.submit(modalForm);

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        '/users/u2/password',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ new_password: 'NewSecurePassword!' }),
        })
      );
    });

    await waitFor(() => {
      expect(
        screen.queryByRole('heading', { name: /reset password/i })
      ).not.toBeInTheDocument();
    });
  });

  it('shows error when password reset API fails', async () => {
    loadUsersOk();
    (apiRequest as any).mockRejectedValueOnce(new Error('Weak password rejected'));

    render(<UserManagementPage />);
    await waitFor(() =>
      expect(screen.getAllByText('bob').length).toBeGreaterThanOrEqual(1)
    );

    const resetBtns = screen.getAllByTitle('Reset password');
    fireEvent.click(resetBtns[1]);

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /reset password/i })
      ).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText('Enter new password'), {
      target: { value: 'bad' },
    });

    const modalForm = screen.getByPlaceholderText('Enter new password').closest('form')!;
    fireEvent.submit(modalForm);

    await waitFor(() => {
      expect(screen.getByText('Weak password rejected')).toBeInTheDocument();
    });
  });

  it('keeps reset password submit disabled when password field is empty', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() =>
      expect(screen.getAllByText('bob').length).toBeGreaterThanOrEqual(1)
    );

    const resetBtns = screen.getAllByTitle('Reset password');
    fireEvent.click(resetBtns[1]);

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /reset password/i })
      ).toBeInTheDocument();
    });

    const modalForm = screen.getByPlaceholderText('Enter new password').closest('form')!;
    const submitBtn = modalForm.querySelector('button[type="submit"]') as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });

  // ========================================================================
  // Navigation
  // ========================================================================

  it('navigates home when Back button is clicked', async () => {
    loadUsersOk();

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const backBtn = screen.getByRole('button', { name: /back/i });
    fireEvent.click(backBtn);

    expect(mockNavigate).toHaveBeenCalledWith('/');
  });
});
