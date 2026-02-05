import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import AgentAlertBanner from './AgentAlertBanner';

const apiRequest = vi.fn();
const navigate = vi.fn();

vi.mock('../../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
}));

describe('AgentAlertBanner', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    navigate.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('renders alerts and allows dismiss/navigation', async () => {
    apiRequest.mockResolvedValue({
      alerts: [
        {
          agent_id: 'agent-1',
          agent_name: 'Agent One',
          error_message: 'Disconnected',
          error_since: new Date(Date.now() - 60000).toISOString(),
        },
      ],
      agent_error_count: 1,
    });

    render(<AgentAlertBanner />);

    expect(await screen.findByText('Agent Error')).toBeInTheDocument();
    fireEvent.click(screen.getByText('View Details'));
    expect(navigate).toHaveBeenCalledWith('/hosts');

    fireEvent.click(screen.getByTitle('Dismiss (will reappear on page refresh)'));
    await waitFor(() => {
      expect(screen.queryByText('Agent Error')).toBeNull();
    });
  });
});
