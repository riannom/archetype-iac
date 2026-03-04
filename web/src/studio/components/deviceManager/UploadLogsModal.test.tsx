import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import UploadLogsModal from './UploadLogsModal';
import type { ImageManagementLogEntry, ImageManagementLogFilter } from './deviceManagerTypes';

// Mock Modal to render children directly
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
      <div data-testid="modal" data-title={title}>
        <button data-testid="modal-close" onClick={onClose}>
          Close
        </button>
        {children}
      </div>
    ) : null,
}));

// Mock utils
vi.mock('./deviceManagerUtils', () => ({
  formatImageLogTime: (ts: string) => `time:${ts}`,
  formatImageLogDate: (ts: string) => `date:${ts}`,
}));

// ============================================================================
// Helpers
// ============================================================================

function makeLogEntry(overrides: Partial<ImageManagementLogEntry> = {}): ImageManagementLogEntry {
  return {
    id: 'log-1',
    timestamp: '2026-01-15T10:30:00Z',
    level: 'info',
    category: 'docker',
    phase: 'upload',
    message: 'Image uploaded successfully',
    ...overrides,
  };
}

function defaultProps() {
  return {
    isOpen: true,
    onClose: vi.fn(),
    imageManagementLogs: [] as ImageManagementLogEntry[],
    filteredImageManagementLogs: [] as ImageManagementLogEntry[],
    imageLogFilter: 'all' as ImageManagementLogFilter,
    setImageLogFilter: vi.fn(),
    imageLogSearch: '',
    setImageLogSearch: vi.fn(),
    imageLogCounts: { all: 0, errors: 0, iso: 0, docker: 0, qcow2: 0 },
    uploadErrorCount: 0,
    copiedUploadLogId: null as string | null,
    clearImageManagementLogs: vi.fn(),
    copyUploadLogEntry: vi.fn(),
  };
}

describe('UploadLogsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Visibility ──

  it('renders when isOpen is true', () => {
    render(<UploadLogsModal {...defaultProps()} />);
    expect(screen.getByTestId('modal')).toBeInTheDocument();
  });

  it('does not render when isOpen is false', () => {
    const props = defaultProps();
    props.isOpen = false;
    render(<UploadLogsModal {...props} />);
    expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
  });

  // ── Empty state ──

  it('shows empty state when no logs exist', () => {
    render(<UploadLogsModal {...defaultProps()} />);
    expect(screen.getByText('No logs found')).toBeInTheDocument();
    expect(screen.getByText('No upload or processing events recorded yet.')).toBeInTheDocument();
  });

  it('shows no-match state when logs exist but filtered list is empty', () => {
    const props = defaultProps();
    props.imageManagementLogs = [makeLogEntry()];
    props.filteredImageManagementLogs = [];
    render(<UploadLogsModal {...props} />);

    expect(screen.getByText('No matching logs')).toBeInTheDocument();
    expect(screen.getByText('No log entries match the current filter.')).toBeInTheDocument();
  });

  // ── Log entries rendering ──

  it('renders log entries in a table', () => {
    const entry = makeLogEntry({
      id: 'log-a',
      level: 'info',
      category: 'docker',
      phase: 'upload',
      message: 'Image uploaded OK',
    });
    const props = defaultProps();
    props.imageManagementLogs = [entry];
    props.filteredImageManagementLogs = [entry];
    render(<UploadLogsModal {...props} />);

    expect(screen.getByText('Image uploaded OK')).toBeInTheDocument();
    expect(screen.getByText('info')).toBeInTheDocument();
    expect(screen.getByText('docker')).toBeInTheDocument();
    expect(screen.getByText('upload')).toBeInTheDocument();
  });

  it('renders entry filename when present', () => {
    const entry = makeLogEntry({ filename: 'ceos.tar' });
    const props = defaultProps();
    props.imageManagementLogs = [entry];
    props.filteredImageManagementLogs = [entry];
    render(<UploadLogsModal {...props} />);

    expect(screen.getByText(/file: ceos\.tar/)).toBeInTheDocument();
  });

  it('renders entry details when present', () => {
    const entry = makeLogEntry({ details: 'Stack trace here' });
    const props = defaultProps();
    props.imageManagementLogs = [entry];
    props.filteredImageManagementLogs = [entry];
    render(<UploadLogsModal {...props} />);

    expect(screen.getByText('Stack trace here')).toBeInTheDocument();
  });

  it('uses formatted time and date', () => {
    const entry = makeLogEntry({ timestamp: '2026-03-01T12:00:00Z' });
    const props = defaultProps();
    props.imageManagementLogs = [entry];
    props.filteredImageManagementLogs = [entry];
    render(<UploadLogsModal {...props} />);

    expect(screen.getByText('time:2026-03-01T12:00:00Z')).toBeInTheDocument();
    expect(screen.getByText('date:2026-03-01T12:00:00Z')).toBeInTheDocument();
  });

  // ── Summary line ──

  it('displays showing count', () => {
    const entries = [makeLogEntry({ id: 'a' }), makeLogEntry({ id: 'b' })];
    const props = defaultProps();
    props.imageManagementLogs = entries;
    props.filteredImageManagementLogs = [entries[0]];
    render(<UploadLogsModal {...props} />);

    expect(screen.getByText('Showing 1 of 2 entries')).toBeInTheDocument();
  });

  it('displays error count when uploadErrorCount > 0', () => {
    const props = defaultProps();
    props.imageManagementLogs = [makeLogEntry()];
    props.uploadErrorCount = 3;
    render(<UploadLogsModal {...props} />);

    expect(screen.getByText('3 errors')).toBeInTheDocument();
  });

  it('does not display error count when uploadErrorCount is 0', () => {
    const props = defaultProps();
    props.imageManagementLogs = [makeLogEntry()];
    props.uploadErrorCount = 0;
    render(<UploadLogsModal {...props} />);

    expect(screen.queryByText(/errors/)).not.toBeInTheDocument();
  });

  // ── Filter select ──

  it('renders filter select with counts', () => {
    const props = defaultProps();
    props.imageLogCounts = { all: 10, errors: 2, iso: 1, docker: 5, qcow2: 2 };
    render(<UploadLogsModal {...props} />);

    const select = screen.getByLabelText('Image log filter') as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.value).toBe('all');

    const options = select.querySelectorAll('option');
    expect(options).toHaveLength(5);
    expect(options[0].textContent).toBe('All (10)');
    expect(options[1].textContent).toBe('Errors (2)');
    expect(options[2].textContent).toBe('ISO (1)');
    expect(options[3].textContent).toBe('Docker (5)');
    expect(options[4].textContent).toBe('QCOW2 (2)');
  });

  it('calls setImageLogFilter on filter change', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.imageLogCounts = { all: 5, errors: 1, iso: 0, docker: 3, qcow2: 1 };
    render(<UploadLogsModal {...props} />);

    const select = screen.getByLabelText('Image log filter');
    await user.selectOptions(select, 'errors');

    expect(props.setImageLogFilter).toHaveBeenCalledWith('errors');
  });

  // ── Search input ──

  it('renders search input with current value', () => {
    const props = defaultProps();
    props.imageLogSearch = 'test query';
    render(<UploadLogsModal {...props} />);

    const input = screen.getByLabelText('Search image logs') as HTMLInputElement;
    expect(input.value).toBe('test query');
  });

  it('calls setImageLogSearch on input change', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<UploadLogsModal {...props} />);

    const input = screen.getByLabelText('Search image logs');
    await user.type(input, 'hello');

    expect(props.setImageLogSearch).toHaveBeenCalled();
  });

  // ── Clear button ──

  it('enables clear button when logs exist', () => {
    const props = defaultProps();
    props.imageManagementLogs = [makeLogEntry()];
    render(<UploadLogsModal {...props} />);

    const btn = screen.getByText('Clear History');
    expect(btn).not.toBeDisabled();
  });

  it('disables clear button when no logs', () => {
    render(<UploadLogsModal {...defaultProps()} />);

    const btn = screen.getByText('Clear History');
    expect(btn).toBeDisabled();
  });

  it('calls clearImageManagementLogs on clear button click', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.imageManagementLogs = [makeLogEntry()];
    render(<UploadLogsModal {...props} />);

    await user.click(screen.getByText('Clear History'));
    expect(props.clearImageManagementLogs).toHaveBeenCalledTimes(1);
  });

  // ── Copy button ──

  it('calls copyUploadLogEntry on copy button click', async () => {
    const user = userEvent.setup();
    const entry = makeLogEntry({ id: 'log-copy' });
    const props = defaultProps();
    props.imageManagementLogs = [entry];
    props.filteredImageManagementLogs = [entry];
    render(<UploadLogsModal {...props} />);

    await user.click(screen.getByText('Copy'));
    expect(props.copyUploadLogEntry).toHaveBeenCalledWith(entry);
  });

  it('shows "Copied" when copiedUploadLogId matches entry', () => {
    const entry = makeLogEntry({ id: 'log-copied' });
    const props = defaultProps();
    props.imageManagementLogs = [entry];
    props.filteredImageManagementLogs = [entry];
    props.copiedUploadLogId = 'log-copied';
    render(<UploadLogsModal {...props} />);

    expect(screen.getByText('Copied')).toBeInTheDocument();
    expect(screen.queryByText('Copy')).not.toBeInTheDocument();
  });

  // ── Close ──

  it('calls onClose when modal close is triggered', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<UploadLogsModal {...props} />);

    await user.click(screen.getByTestId('modal-close'));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });
});
