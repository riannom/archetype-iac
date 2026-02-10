import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { ProtectedRoute } from './ProtectedRoute';
import type { User, GlobalRole } from '../contexts/UserContext';

vi.mock('react-router-dom', () => ({
  Navigate: vi.fn(({ to }: { to: string }) => <div data-testid="navigate" data-to={to} />),
}));

vi.mock('../contexts/UserContext', () => ({
  useUser: vi.fn(),
}));

vi.mock('../utils/permissions', () => ({
  hasMinGlobalRole: vi.fn((user: User | null, minRole: GlobalRole) => {
    if (!user) return false;
    const ranks: Record<GlobalRole, number> = { super_admin: 4, admin: 3, operator: 2, viewer: 1 };
    return ranks[user.global_role] >= ranks[minRole];
  }),
}));

const { useUser } = await import('../contexts/UserContext');

function createUser(global_role: GlobalRole): User {
  return {
    id: '1',
    username: 'testuser',
    email: 'test@example.com',
    is_active: true,
    global_role,
    created_at: '2025-01-01T00:00:00Z',
  };
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns null while loading', () => {
    vi.mocked(useUser).mockReturnValue({
      user: null,
      loading: true,
      setUser: vi.fn(),
    });

    const { container } = render(
      <ProtectedRoute minRole="viewer">
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(container.firstChild).toBeNull();
  });

  it('redirects to /login when no user', () => {
    vi.mocked(useUser).mockReturnValue({
      user: null,
      loading: false,
      setUser: vi.fn(),
    });

    const { getByTestId } = render(
      <ProtectedRoute minRole="viewer">
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    const navigate = getByTestId('navigate');
    expect(navigate).toHaveAttribute('data-to', '/login');
  });

  it('redirects to fallbackPath when insufficient role', () => {
    vi.mocked(useUser).mockReturnValue({
      user: createUser('viewer'),
      loading: false,
      setUser: vi.fn(),
    });

    const { getByTestId } = render(
      <ProtectedRoute minRole="admin">
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    const navigate = getByTestId('navigate');
    expect(navigate).toHaveAttribute('data-to', '/');
  });

  it('renders children when role is sufficient', () => {
    vi.mocked(useUser).mockReturnValue({
      user: createUser('admin'),
      loading: false,
      setUser: vi.fn(),
    });

    const { getByText } = render(
      <ProtectedRoute minRole="operator">
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(getByText('Protected Content')).toBeInTheDocument();
  });

  it('uses custom fallbackPath', () => {
    vi.mocked(useUser).mockReturnValue({
      user: createUser('operator'),
      loading: false,
      setUser: vi.fn(),
    });

    const { getByTestId } = render(
      <ProtectedRoute minRole="admin" fallbackPath="/unauthorized">
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    const navigate = getByTestId('navigate');
    expect(navigate).toHaveAttribute('data-to', '/unauthorized');
  });

  it('renders children when user has exact minimum role', () => {
    vi.mocked(useUser).mockReturnValue({
      user: createUser('operator'),
      loading: false,
      setUser: vi.fn(),
    });

    const { getByText } = render(
      <ProtectedRoute minRole="operator">
        <div>Operator Content</div>
      </ProtectedRoute>
    );

    expect(getByText('Operator Content')).toBeInTheDocument();
  });

  it('renders children when user has higher role than minimum', () => {
    vi.mocked(useUser).mockReturnValue({
      user: createUser('super_admin'),
      loading: false,
      setUser: vi.fn(),
    });

    const { getByText } = render(
      <ProtectedRoute minRole="viewer">
        <div>Super Admin Content</div>
      </ProtectedRoute>
    );

    expect(getByText('Super Admin Content')).toBeInTheDocument();
  });
});
