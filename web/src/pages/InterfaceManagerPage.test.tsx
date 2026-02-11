import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import InterfaceManagerPage from './InterfaceManagerPage';

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({ user: { id: 'u1' } }),
}));

vi.mock('../utils/permissions', () => ({
  canViewInfrastructure: () => true,
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

describe('InterfaceManagerPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads interfaces and agents', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ interfaces: [] });

    render(<InterfaceManagerPage />);

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith('/agents');
      expect(apiRequest).toHaveBeenCalledWith('/infrastructure/interfaces');
    });
  });

  it('opens create modal and calls create API', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ interfaces: [] })
      .mockResolvedValueOnce({ interfaces: [{ name: 'eth0', mtu: 1500, is_physical: true, state: 'up' }] })
      .mockResolvedValueOnce({});

    render(<InterfaceManagerPage />);

    fireEvent.click(await screen.findByText('Create Interface'));

    const hostSelect = screen.getByLabelText('Host');
    fireEvent.change(hostSelect, { target: { value: 'agent-1' } });

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/infrastructure/agents/agent-1/interfaces'));

    fireEvent.click(screen.getByText('Create'));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/infrastructure/agents/agent-1/managed-interfaces', expect.anything()));
  });

  it('disables create when CIDR is invalid', async () => {
    (apiRequest as any)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ interfaces: [] })
      .mockResolvedValueOnce({ interfaces: [{ name: 'eth0', mtu: 1500, is_physical: true, state: 'up' }] });

    render(<InterfaceManagerPage />);

    fireEvent.click(await screen.findByText('Create Interface'));
    const hostSelect = screen.getByLabelText('Host');
    fireEvent.change(hostSelect, { target: { value: 'agent-1' } });

    const ipInput = screen.getByPlaceholderText('e.g. 10.100.0.1/24');
    fireEvent.change(ipInput, { target: { value: '999.999.999.999/99' } });

    const buttons = screen.getAllByRole('button', { name: 'Create' });
    const submitButton = buttons[buttons.length - 1];
    expect(submitButton).toBeDisabled();
  });
});
