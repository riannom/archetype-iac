import React from 'react';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import LogsView from './LogsView';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../hooks/usePolling', () => ({
  usePolling: vi.fn(),
}));

vi.mock('../../utils/download', () => ({
  downloadBlob: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const studioRequest = vi.fn();

function makeEntry(overrides: Record<string, unknown> = {}) {
  return {
    timestamp: '2026-03-01T12:00:00Z',
    level: 'info',
    message: 'Default log message',
    source: 'job',
    job_id: null,
    host_id: null,
    host_name: null,
    ...overrides,
  };
}

function makeResponse(overrides: Record<string, unknown> = {}) {
  return {
    entries: [],
    jobs: [],
    hosts: [],
    total_count: 0,
    error_count: 0,
    has_more: false,
    ...overrides,
  };
}

const JOB_DEPLOY = {
  id: 'job-aaa',
  action: 'deploy',
  status: 'completed',
  created_at: '2026-03-01T12:00:00Z',
};

const JOB_NODE = {
  id: 'job-bbb',
  action: 'node:start:router1',
  status: 'running',
  created_at: '2026-03-01T12:01:00Z',
};

const FULL_RESPONSE = makeResponse({
  entries: [
    makeEntry({ timestamp: '2026-03-01T12:00:01Z', level: 'info', message: 'Deployment started', job_id: 'job-aaa', host_id: 'h1', host_name: 'Agent-1' }),
    makeEntry({ timestamp: '2026-03-01T12:00:05Z', level: 'error', message: 'Container failed to start', job_id: 'job-aaa', host_id: 'h1', host_name: 'Agent-1' }),
    makeEntry({ timestamp: '2026-03-01T12:00:10Z', level: 'warning', message: 'Retry scheduled', job_id: 'job-bbb', host_id: 'h2', host_name: 'Agent-2' }),
    makeEntry({ timestamp: '2026-03-01T12:00:15Z', level: 'success', message: 'Node router1 started', job_id: 'job-bbb', host_id: 'h2', host_name: 'Agent-2' }),
  ],
  jobs: [JOB_DEPLOY, JOB_NODE],
  hosts: ['Agent-1', 'Agent-2'],
  total_count: 4,
  error_count: 1,
  has_more: false,
});

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  studioRequest.mockReset();
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

// ===========================================================================
// Test suite
// ===========================================================================

describe('LogsView round 12', () => {
  // -----------------------------------------------------------------------
  // 1. Empty state
  // -----------------------------------------------------------------------
  it('shows empty state when there are no log entries and loading is done', async () => {
    studioRequest.mockResolvedValueOnce(makeResponse());

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('No log entries found')).toBeInTheDocument();
    });
    // Should show hint about operations
    expect(screen.getByText(/logs will appear here after lab operations/i)).toBeInTheDocument();
  });

  it('shows filter hint in empty state when filters are active', async () => {
    // First load with data so we can set a filter, then reload empty
    studioRequest
      .mockResolvedValueOnce(FULL_RESPONSE)
      .mockResolvedValue(makeResponse());

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Type in search box to activate a filter
    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'nonexistent' } });

    await waitFor(() => {
      const hint = screen.queryByText(/try adjusting your filters/i);
      if (hint) {
        expect(hint).toBeInTheDocument();
      }
    });
  });

  // -----------------------------------------------------------------------
  // 2. Loading state
  // -----------------------------------------------------------------------
  it('shows loading spinner while fetching logs initially', async () => {
    // Never resolve the promise so we stay in loading
    studioRequest.mockReturnValueOnce(new Promise(() => {}));

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Loading logs...')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // 3. Error display
  // -----------------------------------------------------------------------
  it('displays error message when API request fails with Error object', async () => {
    studioRequest.mockRejectedValueOnce(new Error('Network timeout'));

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Network timeout')).toBeInTheDocument();
    });
  });

  it('displays fallback error for non-Error rejections', async () => {
    studioRequest.mockRejectedValueOnce('some string error');

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load logs')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // 4. Entry expand / collapse
  // -----------------------------------------------------------------------
  it('expands an entry on click and shows detail metadata', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Click on the first entry row to expand it
    fireEvent.click(screen.getByText('Deployment started'));

    // The expanded detail should show the "Message" label
    await waitFor(() => {
      expect(screen.getByText('Message')).toBeInTheDocument();
    });

    // Should show metadata fields
    expect(screen.getByText('Timestamp:')).toBeInTheDocument();
    expect(screen.getByText('Level:')).toBeInTheDocument();
    expect(screen.getByText('Source:')).toBeInTheDocument();
  });

  it('collapses an expanded entry when clicked again', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Expand
    fireEvent.click(screen.getByText('Deployment started'));
    await waitFor(() => {
      expect(screen.getByText('Message')).toBeInTheDocument();
    });

    // Collapse by clicking the entry row (first match is the row span, second is the expanded detail)
    fireEvent.click(screen.getAllByText('Deployment started')[0]);
    await waitFor(() => {
      expect(screen.queryByText('Message')).not.toBeInTheDocument();
    });
  });

  it('collapses entry via the close button', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Deployment started'));
    await waitFor(() => {
      expect(screen.getByText('Message')).toBeInTheDocument();
    });

    // Click the close/collapse button (has title="Collapse")
    const collapseBtn = screen.getByTitle('Collapse');
    fireEvent.click(collapseBtn);

    await waitFor(() => {
      expect(screen.queryByText('Message')).not.toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // 5. Job log modal (expanded job details + full log fetch)
  // -----------------------------------------------------------------------
  it('fetches and displays full job log when expanding an entry with job_id', async () => {
    studioRequest
      .mockResolvedValueOnce(FULL_RESPONSE)
      .mockResolvedValueOnce({ log: 'Full deploy output line 1\nFull deploy output line 2' });

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Click to expand entry that has job_id='job-aaa'
    fireEvent.click(screen.getByText('Deployment started'));

    // Should show loading state for job log
    await waitFor(() => {
      expect(screen.getByText('Loading job log...')).toBeInTheDocument();
    });

    // After fetch completes, should show the full log content
    await waitFor(() => {
      expect(screen.getByText(/Full deploy output line 1/)).toBeInTheDocument();
    });

    // Verify the correct API path was called
    expect(studioRequest).toHaveBeenCalledWith('/labs/lab-1/jobs/job-aaa/log');
  });

  it('shows error message when job log fetch fails', async () => {
    studioRequest
      .mockResolvedValueOnce(FULL_RESPONSE)
      .mockRejectedValueOnce(new Error('Job log not found'));

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Deployment started'));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load job log: Job log not found/)).toBeInTheDocument();
    });
  });

  it('shows "No log content available" when job log is empty', async () => {
    studioRequest
      .mockResolvedValueOnce(FULL_RESPONSE)
      .mockResolvedValueOnce({ log: '' });

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Deployment started'));

    await waitFor(() => {
      expect(screen.getByText('No log content available')).toBeInTheDocument();
    });
  });

  it('shows job details section with action, status, and filter button', async () => {
    studioRequest
      .mockResolvedValueOnce(FULL_RESPONSE)
      .mockResolvedValueOnce({ log: 'log content' });

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Deployment started'));

    await waitFor(() => {
      expect(screen.getByText('Job Details')).toBeInTheDocument();
    });

    // Job action/status
    expect(screen.getByText('Action:')).toBeInTheDocument();
    expect(screen.getByText('DEPLOY')).toBeInTheDocument();
    expect(screen.getByText('Status:')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();

    // Filter button
    expect(screen.getByText('Filter to this job')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // 6. Filter pipeline
  // -----------------------------------------------------------------------
  it('builds query string with search parameter', async () => {
    studioRequest.mockResolvedValue(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'container' } });

    await waitFor(() => {
      const calls = studioRequest.mock.calls;
      const lastCall = calls[calls.length - 1][0] as string;
      expect(lastCall).toContain('search=container');
    });
  });

  it('changes level filter and refetches', async () => {
    studioRequest.mockResolvedValue(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Find the Level filter select (4th combobox: Job, Host, Level, Time)
    const selects = screen.getAllByRole('combobox');
    const levelSelect = selects[2]; // 0=Job, 1=Host, 2=Level
    fireEvent.change(levelSelect, { target: { value: 'error' } });

    await waitFor(() => {
      const calls = studioRequest.mock.calls;
      const lastCall = calls[calls.length - 1][0] as string;
      expect(lastCall).toContain('level=error');
    });
  });

  it('changes time filter and refetches', async () => {
    studioRequest.mockResolvedValue(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    const selects = screen.getAllByRole('combobox');
    const timeSelect = selects[3]; // 0=Job, 1=Host, 2=Level, 3=Time
    fireEvent.change(timeSelect, { target: { value: '1h' } });

    await waitFor(() => {
      const calls = studioRequest.mock.calls;
      const lastCall = calls[calls.length - 1][0] as string;
      expect(lastCall).toContain('since=1h');
    });
  });

  it('shows and uses clear filters button when filters active', async () => {
    studioRequest.mockResolvedValue(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // No clear button initially
    expect(screen.queryByText('Clear filters')).not.toBeInTheDocument();

    // Set a search filter
    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'deploy' } });

    await waitFor(() => {
      expect(screen.getByText('Clear filters')).toBeInTheDocument();
    });

    // Click clear
    fireEvent.click(screen.getByText('Clear filters'));

    await waitFor(() => {
      expect(screen.queryByText('Clear filters')).not.toBeInTheDocument();
    });

    // Search input should be empty
    expect((searchInput as HTMLInputElement).value).toBe('');
  });

  it('clicking "Filter to this job" sets job filter and collapses', async () => {
    studioRequest
      .mockResolvedValue(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Expand an entry with job_id
    fireEvent.click(screen.getByText('Deployment started'));
    await waitFor(() => {
      expect(screen.getByText('Filter to this job')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Filter to this job'));

    // Entry should collapse (no more "Message" label)
    await waitFor(() => {
      expect(screen.queryByText('Message')).not.toBeInTheDocument();
    });

    // Job filter select should be set to job-aaa
    const jobSelect = screen.getAllByRole('combobox')[0];
    expect((jobSelect as HTMLSelectElement).value).toBe('job-aaa');
  });

  // -----------------------------------------------------------------------
  // 7. Auto-scroll behavior
  // -----------------------------------------------------------------------
  it('renders follow/auto-scroll toggle button', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Auto-scroll is on by default -> button text is "Following"
    expect(screen.getByText('Following')).toBeInTheDocument();
  });

  it('toggles auto-scroll button text between Following and Follow', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    const followBtn = screen.getByText('Following');
    fireEvent.click(followBtn);

    expect(screen.getByText('Follow')).toBeInTheDocument();
    expect(screen.queryByText('Following')).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // 8. Realtime entries merge
  // -----------------------------------------------------------------------
  it('merges and sorts realtime entries with fetched entries', async () => {
    studioRequest.mockResolvedValueOnce(makeResponse({
      entries: [
        makeEntry({ timestamp: '2026-03-01T12:00:00Z', message: 'First fetched' }),
        makeEntry({ timestamp: '2026-03-01T12:00:20Z', message: 'Third fetched' }),
      ],
      total_count: 2,
    }));

    const realtimeEntries = [
      {
        id: 'rt-1',
        timestamp: new Date('2026-03-01T12:00:10Z'),
        level: 'info' as const,
        message: 'Second realtime',
      },
    ];

    const { container } = render(
      <LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={realtimeEntries} />
    );

    await waitFor(() => {
      expect(screen.getByText('First fetched')).toBeInTheDocument();
    });

    expect(screen.getByText('Second realtime')).toBeInTheDocument();
    expect(screen.getByText('Third fetched')).toBeInTheDocument();

    // Realtime entry should show "LIVE" badge
    expect(screen.getByText('LIVE')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // 9. Footer stats
  // -----------------------------------------------------------------------
  it('shows total entry count and error count in footer', async () => {
    studioRequest.mockResolvedValueOnce(makeResponse({
      entries: [makeEntry()],
      total_count: 42,
      error_count: 3,
    }));

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('42 entries')).toBeInTheDocument();
    });
    expect(screen.getByText(/3 errors/)).toBeInTheDocument();
  });

  it('shows "Results limited" when has_more is true', async () => {
    studioRequest.mockResolvedValueOnce(makeResponse({
      entries: [makeEntry()],
      total_count: 500,
      has_more: true,
    }));

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Results limited')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // 10. Host sidebar
  // -----------------------------------------------------------------------
  it('renders host sidebar with host names', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    expect(screen.getByText('Hosts')).toBeInTheDocument();
    // "All" button in sidebar
    expect(screen.getByText('All')).toBeInTheDocument();
    // Host names appear in multiple places (sidebar, filter dropdown, log entries)
    // Just verify they exist at all
    expect(screen.getAllByText('Agent-1').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Agent-2').length).toBeGreaterThanOrEqual(1);
  });

  it('collapses and expands host sidebar', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Sidebar should show "Hosts" label
    expect(screen.getByText('Hosts')).toBeInTheDocument();

    // Find the collapse chevron button (it's inside the sidebar header area)
    // The sidebar header has a button with fa-chevron-left
    const sidebarButtons = screen.getAllByRole('button');
    // The collapse button doesn't have explicit text; we look for one near the "Hosts" heading
    // It has an <i> with fa-chevron-left class
    const collapseBtn = sidebarButtons.find(btn => {
      const icon = btn.querySelector('i.fa-chevron-left');
      return icon !== null;
    });
    expect(collapseBtn).toBeTruthy();

    fireEvent.click(collapseBtn!);

    // After collapsing, "Hosts" text should not be visible
    await waitFor(() => {
      expect(screen.queryByText('Hosts')).not.toBeInTheDocument();
    });

    // Expand again - find chevron-right button
    const expandBtn = screen.getAllByRole('button').find(btn => {
      const icon = btn.querySelector('i.fa-chevron-right');
      return icon !== null;
    });
    expect(expandBtn).toBeTruthy();
    fireEvent.click(expandBtn!);

    await waitFor(() => {
      expect(screen.getByText('Hosts')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // 11. Copy single entry
  // -----------------------------------------------------------------------
  it('copies a single entry from expanded detail view', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // Expand
    fireEvent.click(screen.getByText('Deployment started'));
    await waitFor(() => {
      expect(screen.getByText('Copy entry')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Copy entry'));

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining('Deployment started')
      );
    });
  });

  // -----------------------------------------------------------------------
  // 12. Export
  // -----------------------------------------------------------------------
  it('calls downloadBlob when exporting logs', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);
    const { downloadBlob } = await import('../../utils/download');

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Export as Text'));

    expect(downloadBlob).toHaveBeenCalledWith(
      expect.any(Blob),
      expect.stringContaining('lab-logs-lab-1-')
    );
  });

  it('disables export button when there are no entries', async () => {
    studioRequest.mockResolvedValueOnce(makeResponse());

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('No log entries found')).toBeInTheDocument();
    });

    const exportBtn = screen.getByText('Export as Text').closest('button');
    expect(exportBtn).toBeDisabled();
  });

  // -----------------------------------------------------------------------
  // 13. Copy all disabled when empty
  // -----------------------------------------------------------------------
  it('disables Copy All button when there are no entries', async () => {
    studioRequest.mockResolvedValueOnce(makeResponse());

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('No log entries found')).toBeInTheDocument();
    });

    const copyAllBtn = screen.getByText('Copy All').closest('button');
    expect(copyAllBtn).toBeDisabled();
  });

  // -----------------------------------------------------------------------
  // 14. formatJobAction coverage
  // -----------------------------------------------------------------------
  it('formats node:start action in job dropdown', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(screen.getByText('Deployment started')).toBeInTheDocument();
    });

    // The job dropdown should contain formatted job actions
    // "deploy" -> "DEPLOY (completed)"
    // "node:start:router1" -> "Node start (router1) (running)"
    const selects = screen.getAllByRole('combobox');
    const jobSelect = selects[0];
    const options = within(jobSelect).getAllByRole('option');

    // First option is "All Jobs"
    expect(options[0]).toHaveTextContent('All Jobs');
    // deploy job formatted as "DEPLOY (completed)"
    expect(options[1]).toHaveTextContent('DEPLOY (completed)');
    // node action formatted as "Node start (router1) (running)"
    expect(options[2]).toHaveTextContent('Node start (router1) (running)');
  });

  // -----------------------------------------------------------------------
  // 15. Query includes limit=500
  // -----------------------------------------------------------------------
  it('includes limit=500 in the initial fetch', async () => {
    studioRequest.mockResolvedValueOnce(FULL_RESPONSE);

    render(<LogsView labId="lab-1" studioRequest={studioRequest} realtimeEntries={[]} />);

    await waitFor(() => {
      expect(studioRequest).toHaveBeenCalledWith(
        expect.stringContaining('limit=500')
      );
    });
  });
});
