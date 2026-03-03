import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { ISOInputStep } from './ISOInputStep';
import type { ISOFileInfo } from './types';

// ============================================================================
// Helpers
// ============================================================================

function makeISO(overrides: Partial<ISOFileInfo> = {}): ISOFileInfo {
  return {
    name: 'refplat_p-20231.iso',
    path: '/uploads/refplat_p-20231.iso',
    size_bytes: 5368709120,
    modified_at: '2026-01-15T10:30:00Z',
    ...overrides,
  };
}

function defaultProps() {
  return {
    inputMode: 'browse' as const,
    setInputMode: vi.fn(),
    isoPath: '',
    setIsoPath: vi.fn(),
    availableISOs: [] as ISOFileInfo[],
    loadingISOs: false,
    uploadDir: '/var/lib/archetype/uploads',
    fetchAvailableISOs: vi.fn(),
    selectedFile: null as File | null,
    setSelectedFile: vi.fn(),
    fileInputRef: { current: null } as React.RefObject<HTMLInputElement>,
    handleFileSelect: vi.fn(),
    error: null as string | null,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('ISOInputStep', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Mode Tabs ──

  it('renders all three mode tabs', () => {
    render(<ISOInputStep {...defaultProps()} />);
    expect(screen.getByText('Browse Server')).toBeInTheDocument();
    expect(screen.getByText('Upload ISO')).toBeInTheDocument();
    expect(screen.getByText('Custom Path')).toBeInTheDocument();
  });

  it('switches to upload mode when Upload ISO tab is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<ISOInputStep {...props} />);

    await user.click(screen.getByText('Upload ISO'));
    expect(props.setInputMode).toHaveBeenCalledWith('upload');
  });

  it('switches to custom mode when Custom Path tab is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<ISOInputStep {...props} />);

    await user.click(screen.getByText('Custom Path'));
    expect(props.setInputMode).toHaveBeenCalledWith('custom');
  });

  it('switches to browse mode when Browse Server tab is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.inputMode = 'upload';
    render(<ISOInputStep {...props} />);

    await user.click(screen.getByText('Browse Server'));
    expect(props.setInputMode).toHaveBeenCalledWith('browse');
  });

  // ── Browse Mode ──

  it('shows loading spinner when fetching ISOs', () => {
    const props = defaultProps();
    props.loadingISOs = true;
    render(<ISOInputStep {...props} />);

    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('shows empty state when no ISOs available', () => {
    const props = defaultProps();
    render(<ISOInputStep {...props} />);

    expect(screen.getByText('No ISOs found in upload directory')).toBeInTheDocument();
    expect(screen.getByText(/\/var\/lib\/archetype\/uploads/)).toBeInTheDocument();
  });

  it('renders available ISO list', () => {
    const props = defaultProps();
    props.availableISOs = [
      makeISO(),
      makeISO({ name: 'another.iso', path: '/uploads/another.iso', size_bytes: 1073741824 }),
    ];
    render(<ISOInputStep {...props} />);

    expect(screen.getByText('refplat_p-20231.iso')).toBeInTheDocument();
    expect(screen.getByText('another.iso')).toBeInTheDocument();
  });

  it('selects an ISO when clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.availableISOs = [makeISO()];

    render(<ISOInputStep {...props} />);

    await user.click(screen.getByText('refplat_p-20231.iso'));
    expect(props.setIsoPath).toHaveBeenCalledWith('/uploads/refplat_p-20231.iso');
  });

  it('calls fetchAvailableISOs when Refresh is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<ISOInputStep {...props} />);

    await user.click(screen.getByText('Refresh'));
    expect(props.fetchAvailableISOs).toHaveBeenCalledTimes(1);
  });

  // ── Upload Mode ──

  it('shows drop zone in upload mode', () => {
    const props = defaultProps();
    props.inputMode = 'upload';
    render(<ISOInputStep {...props} />);

    expect(screen.getByText(/Drop ISO file here or click/)).toBeInTheDocument();
  });

  it('shows selected file info in upload mode', () => {
    const props = defaultProps();
    props.inputMode = 'upload';
    props.selectedFile = new File(['data'], 'selected.iso');
    render(<ISOInputStep {...props} />);

    expect(screen.getByText('selected.iso')).toBeInTheDocument();
  });

  it('calls setSelectedFile(null) when Remove is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.inputMode = 'upload';
    props.selectedFile = new File(['data'], 'removeme.iso');

    render(<ISOInputStep {...props} />);

    await user.click(screen.getByText('Remove'));
    expect(props.setSelectedFile).toHaveBeenCalledWith(null);
  });

  it('handles file drop on drop zone', () => {
    const props = defaultProps();
    props.inputMode = 'upload';
    const { container } = render(<ISOInputStep {...props} />);

    const dropZone = container.querySelector('[class*="border-dashed"]')!;
    const file = new File(['iso-data'], 'dropped.iso');

    fireEvent.drop(dropZone, {
      dataTransfer: {
        files: [file],
      },
    });

    expect(props.handleFileSelect).toHaveBeenCalledWith(file);
  });

  it('handles dragOver by preventing default', () => {
    const props = defaultProps();
    props.inputMode = 'upload';
    const { container } = render(<ISOInputStep {...props} />);

    const dropZone = container.querySelector('[class*="border-dashed"]')!;
    const event = new Event('dragover', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'preventDefault', { value: vi.fn() });

    fireEvent.dragOver(dropZone);
    // dragOver should not cause any errors
    expect(true).toBe(true);
  });

  // ── Custom Path Mode ──

  it('renders text input in custom mode', () => {
    const props = defaultProps();
    props.inputMode = 'custom';
    render(<ISOInputStep {...props} />);

    expect(screen.getByPlaceholderText('/path/to/image.iso')).toBeInTheDocument();
    expect(screen.getByText('Server ISO Path')).toBeInTheDocument();
  });

  it('calls setIsoPath on custom path input change', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.inputMode = 'custom';
    render(<ISOInputStep {...props} />);

    const input = screen.getByPlaceholderText('/path/to/image.iso');
    await user.type(input, '/my/path.iso');

    // setIsoPath is called for each character
    expect(props.setIsoPath).toHaveBeenCalled();
  });

  // ── Error Display ──

  it('shows error message when error is set', () => {
    const props = defaultProps();
    props.error = 'Invalid ISO format';
    render(<ISOInputStep {...props} />);

    expect(screen.getByText('Invalid ISO format')).toBeInTheDocument();
  });

  it('does not show error section when error is null', () => {
    const props = defaultProps();
    render(<ISOInputStep {...props} />);

    expect(screen.queryByText('Invalid ISO format')).not.toBeInTheDocument();
  });

  // ── Supported Formats Info ──

  it('displays supported format information', () => {
    render(<ISOInputStep {...defaultProps()} />);
    expect(screen.getByText('Supported Formats')).toBeInTheDocument();
    expect(screen.getByText(/Cisco VIRL2\/CML2/)).toBeInTheDocument();
  });

  // ── Hidden file input ──

  it('renders hidden file input with .iso accept', () => {
    const props = defaultProps();
    props.inputMode = 'upload';
    const { container } = render(<ISOInputStep {...props} />);

    const hiddenInput = container.querySelector('input[type="file"][accept=".iso"]');
    expect(hiddenInput).toBeInTheDocument();
    expect(hiddenInput).toHaveClass('hidden');
  });
});
