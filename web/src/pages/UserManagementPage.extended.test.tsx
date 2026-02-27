import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import UserManagementPage from './UserManagementPage';

vi.mock('../theme/index', () => ({
  useTheme: () => ({ effectiveMode: 'light', toggleMode: vi.fn() }),
  ThemeSelector: () => <div>ThemeSelector</div>,
}));

let mockUser: Record<string, unknown> | null = { id: 'u1', username: 'admin', global_role: 'admin', is_active: true };
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
  default: () => <div>AdminMenuButton</div>,
}));

const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    Navigate: ({ to }: { to: string }) => <div data-testid="redirect">Redirect {to}</div>,
  };
});

const { apiRequest } = await import('../api');

const sampleUsers = [
  { id: 'u1', username: 'admin', email: 'admin@example.com', global_role: 'admin', is_active: true, created_at: '2024-01-01T00:00:00Z' },
  { id: 'u2', username: 'operator1', email: 'op@example.com', global_role: 'operator', is_active: true, created_at: '2024-02-15T00:00:00Z' },
  { id: 'u3', username: 'vieweruser', email: 'view@example.com', global_role: 'viewer', is_active: false, created_at: '2024-03-01T00:00:00Z' },
  { id: 'u4', username: 'superadmin', email: 'super@example.com', global_role: 'super_admin', is_active: true, created_at: '2024-01-01T00:00:00Z' },
];

describe('UserManagementPage extended', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { id: 'u1', username: 'admin', global_role: 'admin', is_active: true };
    mockUserLoading = false;
    mockCanManageUsers = true;
  });

  // ============================================================
  // User CRUD
  // ============================================================

  it('creates a user with valid username and password', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length })
      .mockResolvedValueOnce({ id: 'u5', username: 'newuser', global_role: 'viewer' })
      .mockResolvedValueOnce({ users: [...sampleUsers, { id: 'u5', username: 'newuser', global_role: 'viewer', is_active: true, created_at: new Date().toISOString() }], total: sampleUsers.length + 1 });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    fireEvent.change(screen.getByPlaceholderText('Enter username'), { target: { value: 'newuser' } });
    fireEvent.change(screen.getByPlaceholderText('Enter password'), { target: { value: 'StrongPass123!' } });

    const submitButtons = screen.getAllByRole('button', { name: 'Create User' });
    const submitBtn = submitButtons[submitButtons.length - 1];
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/users', expect.objectContaining({ method: 'POST' }));
    });
  });

  it('validates that username and password are required (button stays disabled)', async () => {
    (apiRequest as any).mockResolvedValue({ users: [], total: 0 });

    render(<UserManagementPage />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/users'));

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    // Submit button should be disabled with empty fields
    const submitButtons = screen.getAllByRole('button', { name: 'Create User' });
    const submitBtn = submitButtons[submitButtons.length - 1] as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
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

    // Email placeholder is "user@example.com"
    const emailInput = screen.getByPlaceholderText('user@example.com');
    fireEvent.change(emailInput, { target: { value: 'test@test.com' } });

    const submitButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/users', expect.objectContaining({ method: 'POST' }));
    });
  });

  it('toggles a user active/inactive via toggle button', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length })
      .mockResolvedValueOnce({ status: 'ok' })
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('operator1')).toBeInTheDocument());

    // Find toggle buttons (title contains "Deactivate" or "Activate")
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

  // ============================================================
  // Password reset
  // ============================================================

  it('opens password reset modal via key icon button', async () => {
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    // Title is "Reset password"
    const resetBtns = screen.getAllByTitle('Reset password');
    expect(resetBtns.length).toBeGreaterThan(0);

    fireEvent.click(resetBtns[0]);
    await waitFor(() => {
      // Modal heading "Reset Password" appears
      const heading = screen.getByRole('heading', { name: /reset password/i });
      expect(heading).toBeInTheDocument();
    });
  });

  it('shows password strength meter for weak password in create modal', async () => {
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    const passwordInput = screen.getByPlaceholderText('Enter password');
    fireEvent.change(passwordInput, { target: { value: 'short' } });

    await waitFor(() => {
      // Weak text includes additional hint: "Weak — use at least 10 characters"
      expect(screen.getByText(/Weak/)).toBeInTheDocument();
    });
  });

  it('shows Strong password strength for long password', async () => {
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

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
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[0]);

    const passwordInput = screen.getByPlaceholderText('Enter password');
    fireEvent.change(passwordInput, { target: { value: '12DigitPass!' } });

    await waitFor(() => {
      // Moderate text includes additional hint
      expect(screen.getByText(/Moderate/)).toBeInTheDocument();
    });
  });

  // ============================================================
  // Edit modal
  // ============================================================

  it('opens edit modal via pen icon button', async () => {
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('operator1')).toBeInTheDocument());

    const editBtns = screen.getAllByTitle('Edit user');
    expect(editBtns.length).toBeGreaterThan(0);

    fireEvent.click(editBtns[0]);
    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });
  });

  it('cancels edit modal without saving', async () => {
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

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
      .mockResolvedValueOnce({ id: 'u2', username: 'operator1', global_role: 'admin' })
      .mockResolvedValueOnce({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => expect(screen.getByText('operator1')).toBeInTheDocument());

    const editBtns = screen.getAllByTitle('Edit user');
    fireEvent.click(editBtns[1]); // Click edit on operator1

    await waitFor(() => {
      expect(screen.getByText('Edit User')).toBeInTheDocument();
    });

    // Submit the form
    const saveBtn = screen.getByRole('button', { name: /save changes/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        expect.stringMatching(/\/users\/u\d+/),
        expect.objectContaining({ method: 'PATCH' })
      );
    });
  });

  // ============================================================
  // Role badges
  // ============================================================

  it('renders role badges for all user types', async () => {
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => {
      expect(screen.getByText('admin')).toBeInTheDocument();
      expect(screen.getByText('operator1')).toBeInTheDocument();
    });

    // Role labels should appear
    expect(screen.getByText('Admin')).toBeInTheDocument();
    expect(screen.getByText('Operator')).toBeInTheDocument();
    expect(screen.getByText('Viewer')).toBeInTheDocument();
    expect(screen.getByText('Super Admin')).toBeInTheDocument();
  });

  // ============================================================
  // Permissions
  // ============================================================

  it('redirects non-admin users', async () => {
    mockCanManageUsers = false;

    render(<UserManagementPage />);

    await waitFor(() => {
      expect(screen.getByTestId('redirect')).toBeInTheDocument();
      expect(screen.getByText(/Redirect \//)).toBeInTheDocument();
    });
  });

  it('shows the Create User button for admin users', async () => {
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => {
      const createBtn = screen.getAllByRole('button', { name: 'Create User' });
      expect(createBtn.length).toBeGreaterThan(0);
    });
  });

  it('shows all users in the table', async () => {
    (apiRequest as any).mockResolvedValue({ users: sampleUsers, total: sampleUsers.length });

    render(<UserManagementPage />);
    await waitFor(() => {
      expect(screen.getByText('admin')).toBeInTheDocument();
      expect(screen.getByText('operator1')).toBeInTheDocument();
      expect(screen.getByText('superadmin')).toBeInTheDocument();
    });
  });
});
