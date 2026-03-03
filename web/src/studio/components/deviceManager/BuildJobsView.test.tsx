import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import BuildJobsView from './BuildJobsView';
import type { IolBuildRow } from './deviceManagerTypes';
import type { ImageLibraryEntry } from '../../types';

// Mock Modal component
vi.mock('../../../components/ui/Modal', () => ({
  Modal: ({
    isOpen,
    onClose,
    title,
    children,
  }: {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
  }) =>
    isOpen ? (
      <div data-testid="modal" role="dialog" aria-label={title}>
        <h2>{title}</h2>
        <button onClick={onClose} data-testid="modal-close">
          Close
        </button>
        {children}
      </div>
    ) : null,
}));

// ============================================================================
// Helpers
// ============================================================================

function makeImage(overrides: Partial<ImageLibraryEntry> = {}): ImageLibraryEntry {
  return {
    id: 'iol-1',
    kind: 'iol',
    reference: 'iou-l3.bin',
    filename: 'iou-l3.bin',
    ...overrides,
  };
}

function makeBuildRow(overrides: Partial<IolBuildRow> = {}): IolBuildRow {
  return {
    image: makeImage(),
    status: 'building',
    buildError: null,
    buildJobId: 'job-abc',
    buildIgnoredAt: null,
    buildIgnoredBy: null,
    dockerReference: null,
    dockerImageId: null,
    ...overrides,
  };
}

function defaultProps() {
  return {
    uploadStatus: null as string | null,
    iolBuildRows: [] as IolBuildRow[],
    hasActiveIolBuilds: false,
    activeIolBuildCount: 0,
    currentIolBuildRows: [] as IolBuildRow[],
    historicalIolBuildRows: [] as IolBuildRow[],
    refreshingIolBuilds: false,
    retryingIolImageId: null as string | null,
    ignoringIolImageId: null as string | null,
    autoRefreshIolBuilds: true,
    setAutoRefreshIolBuilds: vi.fn(),
    refreshIolBuildStatuses: vi.fn(),
    retryIolBuild: vi.fn(),
    ignoreIolBuildFailure: vi.fn(),
    openIolDiagnostics: vi.fn(),
    showIolDiagnostics: false,
    setShowIolDiagnostics: vi.fn(),
    iolDiagnostics: null,
    iolDiagnosticsLoading: false,
    iolDiagnosticsError: null as string | null,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('BuildJobsView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Header ──

  it('renders the Build Jobs heading', () => {
    render(<BuildJobsView {...defaultProps()} />);
    expect(screen.getByText('Build Jobs')).toBeInTheDocument();
  });

  it('renders description text', () => {
    render(<BuildJobsView {...defaultProps()} />);
    expect(screen.getByText(/Track and manage background IOL/)).toBeInTheDocument();
  });

  it('shows active build count badge when builds in progress', () => {
    const props = defaultProps();
    props.hasActiveIolBuilds = true;
    props.activeIolBuildCount = 3;
    render(<BuildJobsView {...props} />);
    expect(screen.getByText(/3 builds in progress/)).toBeInTheDocument();
  });

  it('shows singular text for 1 build in progress', () => {
    const props = defaultProps();
    props.hasActiveIolBuilds = true;
    props.activeIolBuildCount = 1;
    render(<BuildJobsView {...props} />);
    expect(screen.getByText(/1 build in progress/)).toBeInTheDocument();
  });

  it('does not show active badge when no active builds', () => {
    const props = defaultProps();
    props.hasActiveIolBuilds = false;
    render(<BuildJobsView {...props} />);
    expect(screen.queryByText(/builds? in progress/)).not.toBeInTheDocument();
  });

  // ── Upload Status ──

  it('shows upload status text when provided', () => {
    const props = defaultProps();
    props.uploadStatus = 'IOL build retry queued.';
    render(<BuildJobsView {...props} />);
    expect(screen.getByText('IOL build retry queued.')).toBeInTheDocument();
  });

  it('does not show upload status when null', () => {
    const props = defaultProps();
    props.uploadStatus = null;
    const { container } = render(<BuildJobsView {...props} />);
    // Only the heading text and description should exist
    const statusElements = container.querySelectorAll('.text-stone-500');
    // There should be no extra status paragraph
    expect(screen.queryByText('IOL build retry queued.')).not.toBeInTheDocument();
  });

  // ── Empty State ──

  it('shows empty state when no build rows exist', () => {
    render(<BuildJobsView {...defaultProps()} />);
    expect(screen.getByText('No IOL Build Jobs')).toBeInTheDocument();
    expect(screen.getByText(/Import an ISO or upload/)).toBeInTheDocument();
  });

  // ── Current Jobs ──

  it('renders current build rows with status labels', () => {
    const props = defaultProps();
    const buildingRow = makeBuildRow({
      status: 'building',
      image: makeImage({ id: 'iol-1', filename: 'iou-l3.bin' }),
    });
    const failedRow = makeBuildRow({
      status: 'failed',
      image: makeImage({ id: 'iol-2', filename: 'iou-l2.bin' }),
      buildError: 'Docker daemon error',
    });
    props.iolBuildRows = [buildingRow, failedRow];
    props.currentIolBuildRows = [buildingRow, failedRow];

    render(<BuildJobsView {...props} />);

    expect(screen.getByText('iou-l3.bin')).toBeInTheDocument();
    expect(screen.getByText('Building')).toBeInTheDocument();
    expect(screen.getByText('iou-l2.bin')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('Docker daemon error')).toBeInTheDocument();
  });

  it('shows "No pending or failed jobs" when current rows is empty but total is not', () => {
    const props = defaultProps();
    const completeRow = makeBuildRow({ status: 'complete' });
    props.iolBuildRows = [completeRow];
    props.currentIolBuildRows = [];
    props.historicalIolBuildRows = [completeRow];

    render(<BuildJobsView {...props} />);
    expect(screen.getByText(/No pending or failed jobs/)).toBeInTheDocument();
  });

  it('renders job ID for build rows', () => {
    const props = defaultProps();
    const row = makeBuildRow({ buildJobId: 'rq-job-12345' });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    expect(screen.getByText(/rq-job-12345/)).toBeInTheDocument();
  });

  it('renders docker reference for build rows', () => {
    const props = defaultProps();
    const row = makeBuildRow({ dockerReference: 'archetype/iol:v1', status: 'building' });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    expect(screen.getByText(/archetype\/iol:v1/)).toBeInTheDocument();
  });

  it('renders ignored status with ignored by info', () => {
    const props = defaultProps();
    const row = makeBuildRow({
      status: 'ignored',
      buildIgnoredAt: '2026-01-15T10:30:00Z',
      buildIgnoredBy: 'admin@example.com',
    });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('Ignored')).toBeInTheDocument();
    expect(screen.getByText(/admin@example.com/)).toBeInTheDocument();
  });

  // ── Action Buttons ──

  it('calls retryIolBuild when Retry is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const row = makeBuildRow({
      status: 'failed',
      image: makeImage({ id: 'iol-retry-1' }),
    });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    await user.click(screen.getByText('Retry'));

    expect(props.retryIolBuild).toHaveBeenCalledWith('iol-retry-1', false);
  });

  it('calls retryIolBuild with force=true when Force is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const row = makeBuildRow({
      status: 'failed',
      image: makeImage({ id: 'iol-force-1' }),
    });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    await user.click(screen.getByText('Force'));

    expect(props.retryIolBuild).toHaveBeenCalledWith('iol-force-1', true);
  });

  it('calls ignoreIolBuildFailure when Ignore is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const row = makeBuildRow({
      status: 'failed',
      image: makeImage({ id: 'iol-ignore-1' }),
    });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    await user.click(screen.getByText('Ignore'));

    expect(props.ignoreIolBuildFailure).toHaveBeenCalledWith('iol-ignore-1');
  });

  it('disables Retry and Force buttons when build is queued', () => {
    const props = defaultProps();
    const row = makeBuildRow({ status: 'queued' });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('Retry').closest('button')).toBeDisabled();
    expect(screen.getByText('Force').closest('button')).toBeDisabled();
  });

  it('disables Ignore button when status is not failed', () => {
    const props = defaultProps();
    const row = makeBuildRow({ status: 'building' });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('Ignore').closest('button')).toBeDisabled();
  });

  it('shows Retrying... text when retrying', () => {
    const props = defaultProps();
    const row = makeBuildRow({
      status: 'failed',
      image: makeImage({ id: 'iol-retrying' }),
    });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];
    props.retryingIolImageId = 'iol-retrying';

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('Retrying...')).toBeInTheDocument();
  });

  it('shows Ignoring... text when ignoring', () => {
    const props = defaultProps();
    const row = makeBuildRow({
      status: 'failed',
      image: makeImage({ id: 'iol-ignoring' }),
    });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];
    props.ignoringIolImageId = 'iol-ignoring';

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('Ignoring...')).toBeInTheDocument();
  });

  it('calls openIolDiagnostics when Details is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const row = makeBuildRow({
      status: 'failed',
      image: makeImage({ id: 'iol-details-1' }),
    });
    props.iolBuildRows = [row];
    props.currentIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    await user.click(screen.getByText('Details'));

    expect(props.openIolDiagnostics).toHaveBeenCalledWith('iol-details-1');
  });

  // ── Refresh Controls ──

  it('calls refreshIolBuildStatuses when Refresh is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.iolBuildRows = [makeBuildRow()];
    props.currentIolBuildRows = [makeBuildRow()];

    render(<BuildJobsView {...props} />);
    await user.click(screen.getByText('Refresh'));

    expect(props.refreshIolBuildStatuses).toHaveBeenCalledTimes(1);
  });

  it('disables Refresh button when refreshing', () => {
    const props = defaultProps();
    props.iolBuildRows = [makeBuildRow()];
    props.currentIolBuildRows = [makeBuildRow()];
    props.refreshingIolBuilds = true;

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('Refresh').closest('button')).toBeDisabled();
  });

  it('toggles auto-refresh checkbox', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.iolBuildRows = [makeBuildRow()];
    props.currentIolBuildRows = [makeBuildRow()];

    render(<BuildJobsView {...props} />);
    const checkbox = screen.getByRole('checkbox');
    await user.click(checkbox);

    expect(props.setAutoRefreshIolBuilds).toHaveBeenCalledWith(false);
  });

  // ── Build History ──

  it('renders historical build rows section', () => {
    const props = defaultProps();
    const row = makeBuildRow({
      status: 'complete',
      dockerReference: 'archetype/iol:latest',
      image: makeImage({ id: 'iol-done', filename: 'completed.bin' }),
    });
    props.iolBuildRows = [row];
    props.historicalIolBuildRows = [row];

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('Build History')).toBeInTheDocument();
    expect(screen.getByText('1 completed')).toBeInTheDocument();
    expect(screen.getByText('completed.bin')).toBeInTheDocument();
    expect(screen.getByText('Ready')).toBeInTheDocument();
  });

  it('does not render history section when no completed builds', () => {
    const props = defaultProps();
    props.iolBuildRows = [makeBuildRow({ status: 'building' })];
    props.currentIolBuildRows = [makeBuildRow({ status: 'building' })];

    render(<BuildJobsView {...props} />);
    expect(screen.queryByText('Build History')).not.toBeInTheDocument();
  });

  // ── Diagnostics Modal ──

  it('shows diagnostics modal with loading state', () => {
    const props = defaultProps();
    props.showIolDiagnostics = true;
    props.iolDiagnosticsLoading = true;

    render(<BuildJobsView {...props} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('IOL Build Diagnostics')).toBeInTheDocument();
  });

  it('shows diagnostics error', () => {
    const props = defaultProps();
    props.showIolDiagnostics = true;
    props.iolDiagnosticsError = 'Failed to load build diagnostics';

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('Failed to load build diagnostics')).toBeInTheDocument();
  });

  it('shows diagnostics data when loaded', () => {
    const props = defaultProps();
    props.showIolDiagnostics = true;
    props.iolDiagnostics = {
      image_id: 'iol-1',
      filename: 'test.bin',
      status: 'failed',
      build_error: 'Build timeout',
      recommended_action: 'Try again later',
      queue_job: {
        id: 'qjob-1',
        status: 'failed',
        started_at: '2026-01-01T00:00:00Z',
        ended_at: '2026-01-01T01:00:00Z',
        error_log: 'Traceback: ...',
      },
    };

    render(<BuildJobsView {...props} />);
    expect(screen.getByText('test.bin')).toBeInTheDocument();
    // 'failed' appears in both status and queue_job status
    expect(screen.getAllByText('failed').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Build timeout')).toBeInTheDocument();
    expect(screen.getByText('Try again later')).toBeInTheDocument();
    expect(screen.getByText(/qjob-1/)).toBeInTheDocument();
    expect(screen.getByText('Traceback: ...')).toBeInTheDocument();
  });
});
