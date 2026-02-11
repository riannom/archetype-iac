import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

describe('UserManagementPage modal actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('submits create user payload', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce({ users: [], total: 0 })
      .mockResolvedValueOnce({ id: 'u2' });

    render(<UserManagementPage />);

    fireEvent.click(await screen.findAllByRole('button', { name: 'Create User' }).then((buttons) => buttons[0]));

    fireEvent.change(screen.getByPlaceholderText('Enter username'), { target: { value: 'newuser' } });
    fireEvent.change(screen.getByPlaceholderText('Enter password'), { target: { value: 'password123' } });

    const createButtons = screen.getAllByRole('button', { name: 'Create User' });
    fireEvent.click(createButtons[createButtons.length - 1]);

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/users', expect.anything()));
  });
});
