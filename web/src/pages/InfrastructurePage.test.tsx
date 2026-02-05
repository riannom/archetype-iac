import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import InfrastructurePage from './InfrastructurePage';

const apiRequest = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

vi.mock('../theme/index', () => ({
  useTheme: () => ({ effectiveMode: 'light', toggleMode: vi.fn() }),
  ThemeSelector: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div>Theme</div> : null),
}));

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({ user: { is_admin: true }, loading: false }),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useSearchParams: () => [new URLSearchParams(), vi.fn()],
  };
});

describe('InfrastructurePage', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/infrastructure/mesh') {
        return {
          agents: [],
          links: [],
          settings: { overlay_mtu: 1450, mtu_verification_enabled: true },
        };
      }
      if (path === '/agents/detailed') {
        return [];
      }
      if (path === '/agents/updates/latest') {
        return { version: '1.0.0' };
      }
      if (path === '/infrastructure/network-configs') {
        return [];
      }
      return null;
    });
  });

  it('renders infrastructure page shell', async () => {
    render(<InfrastructurePage />);

    await waitFor(() =>
      expect(screen.getByText('Archetype Infrastructure Management')).toBeInTheDocument()
    );
  });
});
