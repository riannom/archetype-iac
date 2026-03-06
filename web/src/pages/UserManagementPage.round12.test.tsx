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

describe('UserManagementPage — round 12 admin flows', () => {
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
  // Loading & error states
  // ========================================================================

  it('shows loading spinner while users are being fetched', async () => {
    // Never resolve the API call so loading stays visible
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

    // Click the dismiss button (the X icon next to the error)
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

    // There should be toggle buttons only for non-self users (u2 and u3)
    const deactivateBtns = screen.queryAllByTitle('Deactivate user');
    const activateBtns = screen.queryAllByTitle('Activate user');
    const allToggle = [...deactivateBtns, ...activateBtns];
    // u2 is active -> deactivate, u3 is inactive -> activate = 2 total
    expect(allToggle).toHaveLength(2);
  });

  // ========================================================================
  // Create user — error handling
  // ========================================================================

  it('shows modal error when create user API fails', async () => {
    loadUsersOk();
    (apiRequest as any).mockRejectedValueOnce(new Error('Username already exists'));

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    // Open create modal
    const createBtns = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createBtns[0]);

    // Fill required fields
    fireEvent.change(screen.getByPlaceholderText('Enter username'), {
      target: { value: 'duplicate' },
    });
    fireEvent.change(screen.getByPlaceholderText('Enter password'), {
      target: { value: 'SomePassword123' },
    });

    // Submit
    const submitBtns = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(submitBtns[submitBtns.length - 1]);

    await waitFor(() => {
      expect(screen.getByText('Username already exists')).toBeInTheDocument();
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

    fireEvent.change(screen.getByPlaceholderText('Enter username'), {
      target: { value: 'newop' },
    });
    fireEvent.change(screen.getByPlaceholderText('Enter password'), {
      target: { value: 'operatorpass123' },
    });

    // Change role to operator (default is "Viewer — Read-only access")
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

  // ========================================================================
  // Edit user — error handling
  // ========================================================================

  it('shows modal error when edit user API fails', async () => {
    loadUsersOk();
    (apiRequest as any).mockRejectedValueOnce(new Error('Permission denied'));

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('bob')).toBeInTheDocument());

    // Open edit modal for bob (second edit button)
    const editBtns = screen.getAllByTitle('Edit user');
    fireEvent.click(editBtns[1]);

    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });

    // Submit edit form
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

    // Edit bob
    const editBtns = screen.getAllByTitle('Edit user');
    fireEvent.click(editBtns[1]);

    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });

    // Check email field is pre-populated
    const emailInput = screen.getByPlaceholderText('user@example.com') as HTMLInputElement;
    expect(emailInput.value).toBe('bob@example.com');

    // Check the editing label shows username (appears in both table and modal)
    const bobElements = screen.getAllByText('bob');
    expect(bobElements.length).toBeGreaterThanOrEqual(2);
  });

  // ========================================================================
  // Password reset — full flow and error
  // ========================================================================

  it('submits password reset successfully', async () => {
    loadUsersOk();
    (apiRequest as any).mockResolvedValueOnce({ status: 'ok' });

    render(<UserManagementPage />);
    await waitFor(() =>
      expect(screen.getAllByText('bob').length).toBeGreaterThanOrEqual(1)
    );

    // Open password modal for bob
    const resetBtns = screen.getAllByTitle('Reset password');
    fireEvent.click(resetBtns[1]);

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /reset password/i })
      ).toBeInTheDocument();
    });

    // Shows the username in the modal subtitle (appears in both table and modal)
    expect(screen.getAllByText('bob').length).toBeGreaterThanOrEqual(2);

    // Enter new password
    fireEvent.change(screen.getByPlaceholderText('Enter new password'), {
      target: { value: 'NewSecurePassword!' },
    });

    // Submit — the modal submit button is type="submit" inside the form
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

    // Modal should close after success
    await waitFor(() => {
      expect(
        screen.queryByRole('heading', { name: /reset password/i })
      ).not.toBeInTheDocument();
    });
  });

  it('shows error when password reset API fails', async () => {
    loadUsersOk();
    (apiRequest as any).mockRejectedValueOnce(
      new Error('Weak password rejected')
    );

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

    // Submit via form
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

    // The submit button inside the modal form should be disabled
    const modalForm = screen.getByPlaceholderText('Enter new password').closest('form')!;
    const submitBtn = modalForm.querySelector('button[type="submit"]') as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });

  // ========================================================================
  // Toggle active/inactive — error path
  // ========================================================================

  it('shows error banner when toggle active API fails', async () => {
    loadUsersOk();
    (apiRequest as any).mockRejectedValueOnce(new Error('Cannot deactivate'));

    render(<UserManagementPage />);
    await waitFor(() =>
      expect(screen.getAllByText('bob').length).toBeGreaterThanOrEqual(1)
    );

    // bob (u2) is active, so his button says "Deactivate user"
    const deactivateBtns = screen.getAllByTitle('Deactivate user');
    fireEvent.click(deactivateBtns[0]);

    await waitFor(() => {
      expect(screen.getByText(/Cannot deactivate/)).toBeInTheDocument();
    });
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

  // ========================================================================
  // Graceful handling of malformed API response
  // ========================================================================

  it('handles API response with missing users array gracefully', async () => {
    (apiRequest as any).mockResolvedValueOnce({ total: 5 });

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByText(/No users found/)).toBeInTheDocument();
    });
  });
});
