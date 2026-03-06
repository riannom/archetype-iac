import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import AdminSettingsPage from './AdminSettingsPage';

const mockNavigate = vi.fn();
const mockToggleMode = vi.fn();
let mockUser: Record<string, unknown> | null = {
  id: 'u1',
  username: 'admin',
  global_role: 'super_admin',
  is_active: true,
};
let mockUserLoading = false;
let mockCanView = true;

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({
    user: mockUser,
    loading: mockUserLoading,
  }),
}));

vi.mock('../utils/permissions', () => ({
  canViewInfrastructure: () => mockCanView,
}));

vi.mock('../components/AdminMenuButton', () => ({
  default: () => <div data-testid="admin-menu-btn">AdminMenuButton</div>,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

const mockGetSettings = vi.fn();
const mockUpdateSettings = vi.fn();

vi.mock('../api', () => ({
  getInfrastructureSettings: (...args: unknown[]) => mockGetSettings(...args),
  updateInfrastructureSettings: (...args: unknown[]) => mockUpdateSettings(...args),
}));

vi.mock('../theme', () => ({
  useTheme: () => ({ effectiveMode: 'dark', toggleMode: mockToggleMode }),
}));

vi.mock('../theme/presets', () => ({
  builtInThemes: [
    { id: 'midnight', name: 'Midnight' },
    { id: 'sakura-sumie', name: 'Sakura Sumie' },
    { id: 'forest', name: 'Forest' },
  ],
}));

vi.mock('../theme/backgrounds', () => ({
  backgroundPatterns: [
    { id: 'floating-lanterns', name: 'Floating Lanterns' },
    { id: 'sakura-redux', name: 'Sakura Redux' },
    { id: 'none', name: 'None' },
  ],
}));

const defaultSettings = {
  login_dark_theme_id: 'midnight',
  login_dark_background_id: 'floating-lanterns',
  login_dark_background_opacity: 50,
  login_light_theme_id: 'sakura-sumie',
  login_light_background_id: 'sakura-redux',
  login_light_background_opacity: 100,
};

describe('AdminSettingsPage — round 12', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { id: 'u1', username: 'admin', global_role: 'super_admin', is_active: true };
    mockUserLoading = false;
    mockCanView = true;
    mockGetSettings.mockResolvedValue(defaultSettings);
    mockUpdateSettings.mockResolvedValue({});
  });

  // ============================================================
  // 1. Dark mode theme select changes propagate to save payload
  // ============================================================
  it('saves updated dark theme selection', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('Dark Mode')).toBeInTheDocument());

    // The first combobox is dark theme
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: 'forest' } });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(mockUpdateSettings).toHaveBeenCalledWith(
        expect.objectContaining({ login_dark_theme_id: 'forest' }),
      );
    });
  });

  // ============================================================
  // 2. Light mode background select changes propagate to save
  // ============================================================
  it('saves updated light background selection', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('Light Mode')).toBeInTheDocument());

    // selects order: dark-theme(0), dark-bg(1), light-theme(2), light-bg(3)
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[3], { target: { value: 'none' } });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(mockUpdateSettings).toHaveBeenCalledWith(
        expect.objectContaining({ login_light_background_id: 'none' }),
      );
    });
  });

  // ============================================================
  // 3. Back button navigates to home
  // ============================================================
  it('navigates to "/" when Back button is clicked', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('ADMIN SETTINGS')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Back to workspace'));

    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  // ============================================================
  // 4. Theme toggle button calls toggleMode
  // ============================================================
  it('calls toggleMode when dark/light toggle is clicked', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('ADMIN SETTINGS')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle(/switch to light mode/i));

    expect(mockToggleMode).toHaveBeenCalledOnce();
  });

  // ============================================================
  // 5. Non-Error load failure shows generic message
  // ============================================================
  it('shows generic error when load rejects with non-Error', async () => {
    mockGetSettings.mockRejectedValue('some string error');

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load settings')).toBeInTheDocument();
    });
  });

  // ============================================================
  // 6. Non-Error save failure shows generic message
  // ============================================================
  it('shows generic error when save rejects with non-Error', async () => {
    mockUpdateSettings.mockRejectedValue(42);

    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('Dark Mode')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(screen.getByText('Failed to save settings')).toBeInTheDocument();
    });
  });

  // ============================================================
  // 7. Full round-trip: modify all fields, verify complete payload
  // ============================================================
  it('sends complete payload with all modified fields on save', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('Dark Mode')).toBeInTheDocument());

    const selects = screen.getAllByRole('combobox');
    // dark theme → forest
    fireEvent.change(selects[0], { target: { value: 'forest' } });
    // dark bg → none
    fireEvent.change(selects[1], { target: { value: 'none' } });
    // light theme → midnight
    fireEvent.change(selects[2], { target: { value: 'midnight' } });
    // light bg → floating-lanterns
    fireEvent.change(selects[3], { target: { value: 'floating-lanterns' } });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(mockUpdateSettings).toHaveBeenCalledWith({
        login_dark_theme_id: 'forest',
        login_dark_background_id: 'none',
        login_dark_background_opacity: 50,
        login_light_theme_id: 'midnight',
        login_light_background_id: 'floating-lanterns',
        login_light_background_opacity: 100,
      });
    });
  });
});
