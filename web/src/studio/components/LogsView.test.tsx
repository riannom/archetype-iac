import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import LogsView from './LogsView';

const studioRequest = vi.fn();

describe('LogsView', () => {
  beforeEach(() => {
    studioRequest.mockReset();
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:url'),
      revokeObjectURL: vi.fn(),
    });
  });

  it('loads logs and allows copy/export', async () => {
    studioRequest.mockResolvedValueOnce({
      entries: [
        {
          timestamp: new Date().toISOString(),
          level: 'info',
          message: 'Hello',
          source: 'job',
          job_id: 'job1',
          host_id: 'host1',
          host_name: 'Host 1',
        },
      ],
      jobs: [
        {
          id: 'job1',
          action: 'deploy',
          status: 'completed',
          created_at: new Date().toISOString(),
        },
      ],
      hosts: ['Host 1'],
      total_count: 1,
      error_count: 0,
      has_more: false,
    });

    render(<LogsView labId="lab1" studioRequest={studioRequest} realtimeEntries={[]} />);

    expect(await screen.findByText('Logs')).toBeInTheDocument();
    expect(await screen.findByText('Hello')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Copy All'));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByText('Export as Text'));
    expect(URL.createObjectURL).toHaveBeenCalled();
  });
});
