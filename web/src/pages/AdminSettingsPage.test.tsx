import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import AdminSettingsPage from './AdminSettingsPage';

const mockNavigate = vi.fn();
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
  default: () => <div>AdminMenuButton</div>,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

const mockGetInfrastructureSettings = vi.fn();
const mockUpdateInfrastructureSettings = vi.fn();

vi.mock('../api', () => ({
  getInfrastructureSettings: (...args: unknown[]) => mockGetInfrastructureSettings(...args),
  updateInfrastructureSettings: (...args: unknown[]) => mockUpdateInfrastructureSettings(...args),
}));

vi.mock('../theme', () => ({
  useTheme: () => ({ effectiveMode: 'dark', toggleMode: vi.fn() }),
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
    mockGetInfrastructureSettings.mockResolvedValue(defaultSettings);
    mockUpdateInfrastructureSettings.mockResolvedValue({});
  });

  // ============================================================
  // Initial load
  // ============================================================

  it('loads settings on mount', async () => {
    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(mockGetInfrastructureSettings).toHaveBeenCalled();
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
    mockGetInfrastructureSettings.mockImplementation(() => new Promise(() => {}));

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Loading settings...')).toBeInTheDocument();
    });
  });

  it('shows error message on settings load failure', async () => {
    mockGetInfrastructureSettings.mockRejectedValue(new Error('Server down'));

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Server down')).toBeInTheDocument();
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
      expect(mockUpdateInfrastructureSettings).toHaveBeenCalledWith(
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
    mockUpdateInfrastructureSettings.mockRejectedValue(new Error('Permission denied'));

    render(<AdminSettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('Dark Mode')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(screen.getByText('Permission denied')).toBeInTheDocument();
    });
  });

  it('disables save button while saving', async () => {
    let resolveSave: () => void;
    mockUpdateInfrastructureSettings.mockImplementation(
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
