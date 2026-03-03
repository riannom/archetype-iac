import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { ISOReviewStep } from './ISOReviewStep';
import type { ScanResponse, ParsedImage, ParsedNodeDefinition } from './types';

// ============================================================================
// Helpers
// ============================================================================

function makeNodeDefinition(overrides: Partial<ParsedNodeDefinition> = {}): ParsedNodeDefinition {
  return {
    id: 'iosv',
    label: 'IOSv',
    description: 'Cisco IOSv Router',
    nature: 'router',
    vendor: 'cisco',
    ram_mb: 512,
    cpus: 1,
    interfaces: ['GigabitEthernet0/0', 'GigabitEthernet0/1'],
    ...overrides,
  };
}

function makeImage(overrides: Partial<ParsedImage> = {}): ParsedImage {
  return {
    id: 'iosv-image-1',
    node_definition_id: 'iosv',
    label: 'IOSv 15.9(3)M7',
    description: 'IOSv Router Image',
    version: '15.9(3)M7',
    disk_image_filename: 'vios.qcow2',
    disk_image_path: '/images/vios.qcow2',
    size_bytes: 134217728,
    image_type: 'qcow2',
    ...overrides,
  };
}

function makeScanResult(overrides: Partial<ScanResponse> = {}): ScanResponse {
  return {
    session_id: 'session-1',
    iso_path: '/uploads/refplat.iso',
    format: 'cml2',
    size_bytes: 5368709120,
    node_definitions: [makeNodeDefinition()],
    images: [makeImage()],
    parse_errors: [],
    ...overrides,
  };
}

function defaultProps() {
  return {
    scanResult: makeScanResult(),
    selectedImages: new Set(['iosv-image-1']),
    toggleImage: vi.fn(),
    selectAll: vi.fn(),
    selectNone: vi.fn(),
    createDevices: true,
    setCreateDevices: vi.fn(),
    error: null as string | null,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('ISOReviewStep', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── ISO Info Section ──

  it('displays ISO filename from path', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.getByText('refplat.iso')).toBeInTheDocument();
  });

  it('displays ISO format and size', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.getByText(/CML2/)).toBeInTheDocument();
    expect(screen.getByText(/5 GB/)).toBeInTheDocument();
  });

  it('displays image count', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('Images Found')).toBeInTheDocument();
  });

  // ── Error Display ──

  it('shows error when error prop is set', () => {
    const props = defaultProps();
    props.error = 'Import failed: session expired';
    render(<ISOReviewStep {...props} />);
    expect(screen.getByText('Import failed: session expired')).toBeInTheDocument();
  });

  it('does not show error section when error is null', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.queryByText('Import failed')).not.toBeInTheDocument();
  });

  // ── Node Definitions ──

  it('displays node definitions with details', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.getByText('Device Types (1)')).toBeInTheDocument();
    expect(screen.getByText('IOSv')).toBeInTheDocument();
    expect(screen.getByText(/512MB RAM/)).toBeInTheDocument();
    expect(screen.getByText(/1 vCPUs/)).toBeInTheDocument();
    expect(screen.getByText(/2 interfaces/)).toBeInTheDocument();
  });

  it('renders correct icon for router nature', () => {
    const { container } = render(<ISOReviewStep {...defaultProps()} />);
    expect(container.querySelector('.fa-arrows-to-dot')).toBeInTheDocument();
  });

  it('renders correct icon for firewall nature', () => {
    const props = defaultProps();
    props.scanResult = makeScanResult({
      node_definitions: [makeNodeDefinition({ nature: 'firewall' })],
    });
    const { container } = render(<ISOReviewStep {...props} />);
    expect(container.querySelector('.fa-shield-halved')).toBeInTheDocument();
  });

  it('renders server icon for generic nature', () => {
    const props = defaultProps();
    props.scanResult = makeScanResult({
      node_definitions: [makeNodeDefinition({ nature: 'server' })],
    });
    const { container } = render(<ISOReviewStep {...props} />);
    expect(container.querySelector('.fa-server')).toBeInTheDocument();
  });

  it('does not render node definitions when none exist', () => {
    const props = defaultProps();
    props.scanResult = makeScanResult({ node_definitions: [] });
    render(<ISOReviewStep {...props} />);
    expect(screen.queryByText('Device Types')).not.toBeInTheDocument();
  });

  // ── Image Selection ──

  it('displays image selection header with counts', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.getByText(/Images to Import \(1 \/ 1\)/)).toBeInTheDocument();
  });

  it('renders image entries with labels and types', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.getByText('IOSv 15.9(3)M7')).toBeInTheDocument();
    expect(screen.getByText('QCOW2')).toBeInTheDocument();
  });

  it('shows image details including version and size', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    // Version appears in both label and detail line
    expect(screen.getAllByText(/15\.9\(3\)M7/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/128 MB/)).toBeInTheDocument();
  });

  it('calls toggleImage when image checkbox is toggled', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<ISOReviewStep {...props} />);

    const checkboxes = screen.getAllByRole('checkbox', { checked: true });
    const checkbox = checkboxes[0];
    await user.click(checkbox);

    expect(props.toggleImage).toHaveBeenCalledWith('iosv-image-1');
  });

  it('calls selectAll when Select All is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<ISOReviewStep {...props} />);

    await user.click(screen.getByText('Select All'));
    expect(props.selectAll).toHaveBeenCalledTimes(1);
  });

  it('calls selectNone when Select None is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<ISOReviewStep {...props} />);

    await user.click(screen.getByText('Select None'));
    expect(props.selectNone).toHaveBeenCalledTimes(1);
  });

  it('shows checked checkbox for selected images', () => {
    const props = defaultProps();
    props.selectedImages = new Set(['iosv-image-1']);
    render(<ISOReviewStep {...props} />);

    // The image checkbox should be checked
    const checkboxes = screen.getAllByRole('checkbox');
    const imageCheckbox = checkboxes.find((cb) =>
      cb.closest('label')?.textContent?.includes('IOSv 15.9')
    );
    expect(imageCheckbox).toBeChecked();
  });

  it('shows unchecked checkbox for deselected images', () => {
    const props = defaultProps();
    props.selectedImages = new Set();
    render(<ISOReviewStep {...props} />);

    const checkboxes = screen.getAllByRole('checkbox');
    const imageCheckbox = checkboxes.find((cb) =>
      cb.closest('label')?.textContent?.includes('IOSv 15.9')
    );
    expect(imageCheckbox).not.toBeChecked();
  });

  it('renders multiple images', () => {
    const props = defaultProps();
    props.scanResult = makeScanResult({
      images: [
        makeImage({ id: 'img-1', label: 'IOSv 15.9' }),
        makeImage({ id: 'img-2', label: 'CSR1000v 17.3', image_type: 'qcow2' }),
      ],
    });
    props.selectedImages = new Set(['img-1', 'img-2']);

    render(<ISOReviewStep {...props} />);
    expect(screen.getByText(/Images to Import \(2 \/ 2\)/)).toBeInTheDocument();
    expect(screen.getByText('IOSv 15.9')).toBeInTheDocument();
    expect(screen.getByText('CSR1000v 17.3')).toBeInTheDocument();
  });

  // ── Create Devices Option ──

  it('renders create devices checkbox', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.getByText('Create device types for new definitions')).toBeInTheDocument();
  });

  it('calls setCreateDevices when checkbox is toggled', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<ISOReviewStep {...props} />);

    // Find the create devices checkbox (last one or by label)
    const checkboxes = screen.getAllByRole('checkbox');
    const createDevicesCheckbox = checkboxes.find(
      (cb) => cb.closest('label')?.textContent?.includes('Create device types')
    );
    expect(createDevicesCheckbox).toBeDefined();
    await user.click(createDevicesCheckbox!);

    expect(props.setCreateDevices).toHaveBeenCalledWith(false);
  });

  // ── Parse Warnings ──

  it('shows parse warnings when present', () => {
    const props = defaultProps();
    props.scanResult = makeScanResult({
      parse_errors: ['Unknown image format: xyz', 'Missing metadata for node foo'],
    });

    render(<ISOReviewStep {...props} />);
    expect(screen.getByText('Parse Warnings')).toBeInTheDocument();
    expect(screen.getByText('Unknown image format: xyz')).toBeInTheDocument();
    expect(screen.getByText('Missing metadata for node foo')).toBeInTheDocument();
  });

  it('does not show parse warnings section when no errors', () => {
    render(<ISOReviewStep {...defaultProps()} />);
    expect(screen.queryByText('Parse Warnings')).not.toBeInTheDocument();
  });
});
