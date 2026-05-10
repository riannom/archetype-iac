import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import LogEntryDetail from './LogEntryDetail';
import type { LabLogEntry, LabLogJob } from '../../api';

const levelColors: Record<string, string> = {
  ERROR: 'text-red-500',
  INFO: 'text-blue-500',
};

function makeEntry(overrides: Partial<LabLogEntry> & { isRealtime?: boolean } = {}): LabLogEntry & { isRealtime?: boolean } {
  return {
    timestamp: '2026-05-06T12:00:00.000Z',
    level: 'ERROR',
    message: 'something broke\nsecond line',
    source: 'job',
    host_name: 'agent-01',
    job_id: 'j-123',
    ...overrides,
  } as LabLogEntry & { isRealtime?: boolean };
}

function makeJob(overrides: Partial<LabLogJob> = {}): LabLogJob {
  return {
    id: 'abcd1234efgh5678',
    action: 'sync:up',
    status: 'completed',
    created_at: '2026-05-06T11:59:00.000Z',
    ...overrides,
  } as LabLogJob;
}

function renderDetail(overrides: Partial<React.ComponentProps<typeof LogEntryDetail>> = {}) {
  const props: React.ComponentProps<typeof LogEntryDetail> = {
    entry: makeEntry(),
    job: makeJob(),
    expandedJobLog: null,
    loadingJobLog: false,
    copiedEntryIdx: null,
    idx: 3,
    levelColors,
    onFilterToJob: vi.fn(),
    onCopyEntry: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  return { ...render(<LogEntryDetail {...props} />), props };
}

describe('LogEntryDetail', () => {
  it('renders the message, level, host, and timestamp', () => {
    renderDetail();
    expect(screen.getByText(/something broke/)).toBeInTheDocument();
    expect(screen.getByText('ERROR')).toBeInTheDocument();
    expect(screen.getByText('agent-01')).toBeInTheDocument();
    expect(screen.getByText('Timestamp:')).toBeInTheDocument();
  });

  it('omits the host row when host_name is empty', () => {
    renderDetail({ entry: makeEntry({ host_name: undefined }) });
    expect(screen.queryByText('Host:')).not.toBeInTheDocument();
  });

  it('falls back to "realtime" source when source is missing and isRealtime is true', () => {
    renderDetail({ entry: makeEntry({ source: undefined, isRealtime: true }) });
    expect(screen.getByText('realtime')).toBeInTheDocument();
  });

  it('falls back to "job" source when source and isRealtime are both absent', () => {
    renderDetail({ entry: makeEntry({ source: undefined }) });
    expect(screen.getByText('job')).toBeInTheDocument();
  });

  it('applies levelColors classes when the level matches a key', () => {
    const { container } = renderDetail();
    const errorBadge = container.querySelector('.text-red-500');
    expect(errorBadge?.textContent).toBe('ERROR');
  });

  it('falls back to text-stone-500 for unknown levels', () => {
    renderDetail({ entry: makeEntry({ level: 'TRACE' }) });
    const traceSpan = screen.getByText('TRACE');
    expect(traceSpan.className).toMatch(/text-stone-500/);
  });

  it('hides the job-details block when job is null', () => {
    renderDetail({ job: null });
    expect(screen.queryByText('Job Details')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Filter to this job/i })).not.toBeInTheDocument();
  });

  it('formats action prefixes (sync: / node:) and uppercases plain actions', () => {
    const { rerender } = renderDetail({ job: makeJob({ action: 'sync:down' }) });
    expect(screen.getByText('Sync down')).toBeInTheDocument();

    rerender(
      <LogEntryDetail
        entry={makeEntry()}
        job={makeJob({ action: 'node:start:agent-01' })}
        expandedJobLog={null}
        loadingJobLog={false}
        copiedEntryIdx={null}
        idx={3}
        levelColors={levelColors}
        onFilterToJob={() => {}}
        onCopyEntry={() => {}}
        onClose={() => {}}
      />
    );
    expect(screen.getByText('Node start (agent-01)')).toBeInTheDocument();

    rerender(
      <LogEntryDetail
        entry={makeEntry()}
        job={makeJob({ action: 'restart' })}
        expandedJobLog={null}
        loadingJobLog={false}
        copiedEntryIdx={null}
        idx={3}
        levelColors={levelColors}
        onFilterToJob={() => {}}
        onCopyEntry={() => {}}
        onClose={() => {}}
      />
    );
    expect(screen.getByText('RESTART')).toBeInTheDocument();
  });

  it('truncates the job id to first 8 chars + ellipsis', () => {
    renderDetail();
    expect(screen.getByText('abcd1234...')).toBeInTheDocument();
  });

  it('shows the loading spinner when loadingJobLog is true', () => {
    const { container } = renderDetail({ loadingJobLog: true });
    expect(screen.getByText(/Loading job log/)).toBeInTheDocument();
    expect(container.querySelector('.fa-spinner')).toBeInTheDocument();
  });

  it('renders the expanded job log in a pre block when supplied', () => {
    renderDetail({ expandedJobLog: 'line1\nline2' });
    const pre = screen.getByText(/line1/).closest('pre');
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain('line2');
  });

  it('clicking close stops propagation and calls onClose', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const parentClick = vi.fn();
    const { container } = render(
      <div onClick={parentClick}>
        <LogEntryDetail
          entry={makeEntry()}
          job={null}
          expandedJobLog={null}
          loadingJobLog={false}
          copiedEntryIdx={null}
          idx={0}
          levelColors={levelColors}
          onFilterToJob={() => {}}
          onCopyEntry={() => {}}
          onClose={onClose}
        />
      </div>
    );
    const closeBtn = container.querySelector('button[title="Collapse"]') as HTMLButtonElement;
    await user.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(parentClick).not.toHaveBeenCalled();
  });

  it('clicking "Filter to this job" calls onFilterToJob with the job id', async () => {
    const user = userEvent.setup();
    const onFilterToJob = vi.fn();
    renderDetail({ onFilterToJob });
    await user.click(screen.getByRole('button', { name: /Filter to this job/i }));
    expect(onFilterToJob).toHaveBeenCalledWith('abcd1234efgh5678');
  });

  it('shows "Copied!" affordance when copiedEntryIdx matches idx', () => {
    renderDetail({ copiedEntryIdx: 3, idx: 3 });
    expect(screen.getByText(/Copied!/)).toBeInTheDocument();
  });

  it('shows "Copy entry" by default and calls onCopyEntry on click', async () => {
    const user = userEvent.setup();
    const onCopyEntry = vi.fn();
    renderDetail({ onCopyEntry, idx: 7 });
    await user.click(screen.getByRole('button', { name: /Copy entry/i }));
    expect(onCopyEntry).toHaveBeenCalledTimes(1);
    expect(onCopyEntry.mock.calls[0][1]).toBe(7);
  });

  it('renders the failed-status row in red when job.status is failed', () => {
    const { container } = renderDetail({ job: makeJob({ status: 'failed' }) });
    const failedSpan = Array.from(container.querySelectorAll('span'))
      .find((s) => s.textContent === 'failed');
    expect(failedSpan).toBeDefined();
    expect(failedSpan!.className).toMatch(/text-red-600/);
  });
});
