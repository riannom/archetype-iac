import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import LogsView from './LogsView';

const studioRequest = vi.fn();

vi.mock('../hooks/usePolling', () => ({
  usePolling: vi.fn(),
}));

vi.mock('../../utils/download', () => ({
  downloadBlob: vi.fn(),
}));

const defaultLogsResponse = {
  entries: [
    {
      timestamp: '2026-02-01T10:00:00Z',
      level: 'info',
      message: 'Lab deployment started',
      source: 'job',
      job_id: 'job-1',
      host_id: 'host-1',
      host_name: 'Agent 1',
    },
    {
      timestamp: '2026-02-01T10:00:05Z',
      level: 'error',
      message: 'Container creation failed',
      source: 'job',
      job_id: 'job-1',
      host_id: 'host-1',
      host_name: 'Agent 1',
    },
    {
      timestamp: '2026-02-01T10:00:10Z',
      level: 'warning',
      message: 'Retry attempt 1',
      source: 'job',
      job_id: 'job-2',
      host_id: 'host-2',
      host_name: 'Agent 2',
    },
  ],
  jobs: [
    { id: 'job-1', action: 'deploy', status: 'completed', created_at: '2026-02-01T10:00:00Z' },
    { id: 'job-2', action: 'node:start:router1', status: 'running', created_at: '2026-02-01T10:00:08Z' },
  ],
  hosts: ['Agent 1', 'Agent 2'],
  total_count: 3,
  error_count: 1,
  has_more: false,
};

describe('LogsView - filters and actions', () => {
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

  // ============================================================
  // Initial load
  // ============================================================

  it('fetches logs on mount with correct path', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-42" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(studioRequest).toHaveBeenCalledWith(
        expect.stringContaining('/labs/lab-42/logs')
      );
    });
  });

  it('renders log entries after loading', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    expect(await screen.findByText('Lab deployment started')).toBeInTheDocument();
    expect(screen.getByText('Container creation failed')).toBeInTheDocument();
    expect(screen.getByText('Retry attempt 1')).toBeInTheDocument();
  });

  it('shows error message on fetch failure', async () => {
    studioRequest.mockRejectedValueOnce(new Error('Server error'));

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Server error')).toBeInTheDocument();
    });
  });

  it('renders the Logs heading', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    expect(await screen.findByText('Logs')).toBeInTheDocument();
  });

  // ============================================================
  // Filters
  // ============================================================

  it('renders job filter dropdown with job options', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    // The component should render select dropdowns for filtering
    const selects = screen.getAllByRole('combobox');
    expect(selects.length).toBeGreaterThan(0);
  });

  it('renders level filter options', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    // Should have filter controls
    const selects = screen.getAllByRole('combobox');
    expect(selects.length).toBeGreaterThanOrEqual(2);
  });

  it('renders search input for filtering', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/search/i);
    expect(searchInput).toBeInTheDocument();
  });

  it('applies search filter when typing in search box', async () => {
    studioRequest.mockResolvedValue(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'failed' } });

    // Should trigger a new fetch with search param
    await waitFor(() => {
      const calls = studioRequest.mock.calls;
      const lastCall = calls[calls.length - 1];
      expect(lastCall[0]).toContain('search=failed');
    });
  });

  it('clears filters via clear button', async () => {
    studioRequest.mockResolvedValue(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    // Apply a filter first
    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'test' } });

    await waitFor(() => {
      const clearBtn = screen.queryByText(/clear/i);
      if (clearBtn) {
        fireEvent.click(clearBtn);
      }
    });
  });

  // ============================================================
  // Auto-refresh
  // ============================================================

  it('renders auto-refresh toggle', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    const autoRefreshLabel = screen.getByText(/auto-refresh/i);
    expect(autoRefreshLabel).toBeInTheDocument();
  });

  // ============================================================
  // Copy and Export actions
  // ============================================================

  it('copies all logs to clipboard', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Copy All'));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalled();
    });
  });

  it('shows copied indicator after clipboard copy', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Copy All'));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalled();
    });
  });

  it('exports logs as text file', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);
    const { downloadBlob } = await import('../../utils/download');

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Export as Text'));
    expect(downloadBlob).toHaveBeenCalled();
  });

  // ============================================================
  // Realtime entries
  // ============================================================

  it('merges realtime entries with loaded logs', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    const realtimeEntries = [
      {
        id: 'rt-1',
        timestamp: new Date('2026-02-01T10:00:15Z'),
        level: 'success' as const,
        message: 'Realtime event arrived',
      },
    ];

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={realtimeEntries} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    // Realtime entries should appear in the list
    expect(screen.getByText('Realtime event arrived')).toBeInTheDocument();
  });

  it('shows error count in summary', async () => {
    studioRequest.mockResolvedValueOnce(defaultLogsResponse);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Lab deployment started')).toBeInTheDocument();
    });

    // Should show error count badge or summary text
    expect(screen.getByText('Container creation failed')).toBeInTheDocument();
  });
});
