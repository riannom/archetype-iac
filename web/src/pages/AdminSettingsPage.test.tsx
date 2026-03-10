import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import AdminSettingsPage from './AdminSettingsPage';

const mockNavigate = vi.fn();
const mockToggleMode = vi.fn();
let mockCanViewInfrastructure = true;

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({
    user: { id: 'u1', username: 'admin', global_role: 'super_admin', is_active: true },
    loading: false,
  }),
}));

vi.mock('../utils/permissions', () => ({
  canViewInfrastructure: () => mockCanViewInfrastructure,
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

describe('AdminSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCanViewInfrastructure = true;
    mockGetSettings.mockResolvedValue(defaultSettings);
    mockUpdateSettings.mockResolvedValue({});
  });

  // ============================================================
  // Initial load
  // ============================================================

  it('loads settings on mount', async () => {
    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(mockGetSettings).toHaveBeenCalled();
    });
  });

  it('renders page header with title', async () => {
    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('ADMIN SETTINGS')).toBeInTheDocument();
      expect(screen.getByText('Global Defaults')).toBeInTheDocument();
    });
  });

  it('populates dark mode defaults from settings', async () => {
    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Dark Mode')).toBeInTheDocument();
    });

    // Theme selects should be populated
    const selects = screen.getAllByRole('combobox');
    expect(selects.length).toBeGreaterThanOrEqual(4); // dark theme, dark bg, light theme, light bg
  });

  it('populates light mode defaults from settings', async () => {
    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Light Mode')).toBeInTheDocument();
    });
  });

  it('shows loading state while fetching settings', async () => {
    mockGetSettings.mockImplementation(() => new Promise(() => {}));

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Loading settings...')).toBeInTheDocument();
    });
  });

  it('shows error message on settings load failure', async () => {
    mockGetSettings.mockRejectedValue(new Error('Server down'));

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Server down')).toBeInTheDocument();
    });
  });

  it('shows generic error when load rejects with non-Error', async () => {
    mockGetSettings.mockRejectedValue('some string error');

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load settings')).toBeInTheDocument();
    });
  });

  // ============================================================
  // Save
  // ============================================================

  it('saves settings successfully and shows "Saved"', async () => {
    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Dark Mode')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(mockUpdateSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          login_dark_theme_id: 'midnight',
          login_light_theme_id: 'sakura-sumie',
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByText('Saved')).toBeInTheDocument();
    });
  });

  it('shows error on save failure', async () => {
    mockUpdateSettings.mockRejectedValue(new Error('Permission denied'));

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Dark Mode')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(screen.getByText('Permission denied')).toBeInTheDocument();
    });
  });

  it('shows generic error when save rejects with non-Error', async () => {
    mockUpdateSettings.mockRejectedValue(42);

    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('Dark Mode')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(screen.getByText('Failed to save settings')).toBeInTheDocument();
    });
  });

  it('disables save button while saving', async () => {
    let resolveSave: () => void;
    mockUpdateSettings.mockImplementation(
      () => new Promise<void>((resolve) => { resolveSave = resolve; })
    );

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Dark Mode')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(screen.getByText('Saving...')).toBeInTheDocument();
    });

    // Clean up
    resolveSave!();
  });

  // ============================================================
  // Theme select changes propagate to save payload
  // ============================================================

  it('saves updated dark theme selection', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('Dark Mode')).toBeInTheDocument());

    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: 'forest' } });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(mockUpdateSettings).toHaveBeenCalledWith(
        expect.objectContaining({ login_dark_theme_id: 'forest' }),
      );
    });
  });

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

  it('sends complete payload with all modified fields on save', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('Dark Mode')).toBeInTheDocument());

    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: 'forest' } });
    fireEvent.change(selects[1], { target: { value: 'none' } });
    fireEvent.change(selects[2], { target: { value: 'midnight' } });
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

  // ============================================================
  // Navigation
  // ============================================================

  it('navigates to "/" when Back button is clicked', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('ADMIN SETTINGS')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Back to workspace'));

    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('calls toggleMode when dark/light toggle is clicked', async () => {
    render(<AdminSettingsPage />);
    await waitFor(() => expect(screen.getByText('ADMIN SETTINGS')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle(/switch to light mode/i));

    expect(mockToggleMode).toHaveBeenCalledOnce();
  });

  // ============================================================
  // Permissions
  // ============================================================

  it('redirects non-admin users to home', async () => {
    mockCanViewInfrastructure = false;

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true });
    });
  });
});
