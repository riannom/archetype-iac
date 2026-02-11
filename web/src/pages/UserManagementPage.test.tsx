import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import UserManagementPage from './UserManagementPage';

vi.mock('../theme/index', () => ({
  useTheme: () => ({ effectiveMode: 'light', toggleMode: vi.fn() }),
  ThemeSelector: () => <div>ThemeSelector</div>,
}));

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({
    user: { id: 'u1', username: 'admin', global_role: 'admin', is_active: true },
    loading: false,
  }),
}));

vi.mock('../utils/permissions', () => ({
  canManageUsers: () => true,
}));

vi.mock('../api', () => ({
  apiRequest: vi.fn(),
}));

vi.mock('../components/AdminMenuButton', () => ({
  default: () => <div>AdminMenuButton</div>,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    Navigate: ({ to }: { to: string }) => <div>Redirect {to}</div>,
  };
});

const { apiRequest } = await import('../api');

describe('UserManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads users and renders table', async () => {
    (apiRequest as any).mockResolvedValue({
      users: [
        { id: 'u1', username: 'admin', email: 'a@example.com', global_role: 'admin', is_active: true, created_at: new Date().toISOString() },
      ],
      total: 1,
    });

    render(<UserManagementPage />);

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/users'));
    expect(screen.getByText('admin')).toBeInTheDocument();
  });

  it('shows create modal and validates empty fields', async () => {
    (apiRequest as any).mockResolvedValue({ users: [], total: 0 });

    render(<UserManagementPage />);

    fireEvent.click(await screen.findAllByRole('button', { name: 'Create User' }).then((buttons) => buttons[0]));
    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[createButtons.length - 1]);

    expect(screen.getByText('Username and password are required.')).toBeInTheDocument();
  });
});
