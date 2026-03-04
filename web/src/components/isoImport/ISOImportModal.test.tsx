import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import ISOImportModal from './ISOImportModal';
import type { Step, InputMode, ScanResponse, ImageProgress } from './types';

// ============================================================================
// Mock useISOUpload hook
// ============================================================================

const mockUpload = {
  step: 'input' as Step,
  setStep: vi.fn(),
  isoPath: '',
  setIsoPath: vi.fn(),
  error: null as string | null,
  scanResult: null as ScanResponse | null,
  selectedImages: new Set<string>(),
  createDevices: true,
  setCreateDevices: vi.fn(),
  importProgress: {} as Record<string, ImageProgress>,
  overallProgress: 0,
  availableISOs: [],
  uploadDir: '/var/lib/archetype/uploads',
  loadingISOs: false,
  inputMode: 'browse' as InputMode,
  setInputMode: vi.fn(),
  selectedFile: null as File | null,
  setSelectedFile: vi.fn(),
  uploadProgress: 0,
  uploadStatus: '',
  fileInputRef: { current: null },
  fetchAvailableISOs: vi.fn(),
  resetState: vi.fn(),
  cleanup: vi.fn(),
  handleScan: vi.fn(),
  handleImport: vi.fn(),
  handleFileSelect: vi.fn(),
  handleUpload: vi.fn(),
  cancelUpload: vi.fn(),
  toggleImage: vi.fn(),
  selectAll: vi.fn(),
  selectNone: vi.fn(),
};

vi.mock('./useISOUpload', () => ({
  useISOUpload: () => mockUpload,
}));

vi.mock('./ISOInputStep', () => ({
  ISOInputStep: () => <div data-testid="iso-input-step">ISOInputStep</div>,
}));

vi.mock('./ISOReviewStep', () => ({
  ISOReviewStep: () => <div data-testid="iso-review-step">ISOReviewStep</div>,
}));

vi.mock('./ISOImportProgress', () => ({
  ISOImportProgress: () => <div data-testid="iso-import-progress">ISOImportProgress</div>,
  UploadProgressStep: () => <div data-testid="upload-progress-step">UploadProgressStep</div>,
}));

// ============================================================================
// Helpers
// ============================================================================

function defaultProps() {
  return {
    isOpen: true,
    onClose: vi.fn(),
    onImportComplete: vi.fn(),
    onLogEvent: vi.fn(),
  };
}

function resetMockUpload() {
  mockUpload.step = 'input';
  mockUpload.isoPath = '';
  mockUpload.error = null;
  mockUpload.scanResult = null;
  mockUpload.selectedImages = new Set<string>();
  mockUpload.inputMode = 'browse';
  mockUpload.selectedFile = null;
  mockUpload.uploadProgress = 0;
  mockUpload.uploadStatus = '';
  mockUpload.overallProgress = 0;
  mockUpload.importProgress = {};
  Object.values(mockUpload).forEach((v) => {
    if (typeof v === 'function' && 'mockClear' in v) {
      (v as ReturnType<typeof vi.fn>).mockClear();
    }
  });
}

// ============================================================================
// Tests
// ============================================================================

describe('ISOImportModal', () => {
  beforeEach(() => {
    resetMockUpload();
  });

  // ── Visibility ──

  it('returns null when isOpen is false', () => {
    const { container } = render(<ISOImportModal {...defaultProps()} isOpen={false} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders modal when isOpen is true', () => {
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Import from ISO')).toBeInTheDocument();
  });

  // ── Header ──

  it('renders modal title and description', () => {
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Import from ISO')).toBeInTheDocument();
    expect(screen.getByText(/Import VM images from vendor ISO files/)).toBeInTheDocument();
  });

  it('calls onClose when header close button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<ISOImportModal {...props} />);

    // The X button in the header
    const closeBtn = screen.getByRole('button', { name: '' });
    // Fallback: find by the fa-xmark icon's parent button
    const headerButtons = document.querySelectorAll('button');
    const xButton = Array.from(headerButtons).find((btn) =>
      btn.querySelector('.fa-xmark')
    );
    expect(xButton).toBeDefined();
    await user.click(xButton!);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  // ── Backdrop ──

  it('calls onClose when backdrop is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    const { container } = render(<ISOImportModal {...props} />);

    // The backdrop is the first absolute div with bg-black
    const backdrop = container.querySelector('.bg-black\\/50');
    expect(backdrop).toBeTruthy();
    await user.click(backdrop!);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  // ── Step: input ──

  it('renders ISOInputStep when step is input', () => {
    mockUpload.step = 'input';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByTestId('iso-input-step')).toBeInTheDocument();
  });

  it('shows Scan ISO button in input step with browse mode', () => {
    mockUpload.step = 'input';
    mockUpload.inputMode = 'browse';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Scan ISO')).toBeInTheDocument();
  });

  it('disables Scan ISO button when isoPath is empty', () => {
    mockUpload.step = 'input';
    mockUpload.inputMode = 'browse';
    mockUpload.isoPath = '';
    render(<ISOImportModal {...defaultProps()} />);
    const scanBtn = screen.getByText('Scan ISO').closest('button');
    expect(scanBtn).toBeDisabled();
  });

  it('enables Scan ISO button when isoPath has value', () => {
    mockUpload.step = 'input';
    mockUpload.inputMode = 'browse';
    mockUpload.isoPath = '/path/to/file.iso';
    render(<ISOImportModal {...defaultProps()} />);
    const scanBtn = screen.getByText('Scan ISO').closest('button');
    expect(scanBtn).not.toBeDisabled();
  });

  it('calls handleScan when Scan ISO is clicked', async () => {
    const user = userEvent.setup();
    mockUpload.step = 'input';
    mockUpload.inputMode = 'custom';
    mockUpload.isoPath = '/path/to/file.iso';
    render(<ISOImportModal {...defaultProps()} />);

    await user.click(screen.getByText('Scan ISO'));
    expect(mockUpload.handleScan).toHaveBeenCalledTimes(1);
  });

  // ── Step: input (upload mode) ──

  it('shows Upload & Scan button in upload mode', () => {
    mockUpload.step = 'input';
    mockUpload.inputMode = 'upload';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Upload & Scan')).toBeInTheDocument();
  });

  it('disables Upload & Scan when no file is selected', () => {
    mockUpload.step = 'input';
    mockUpload.inputMode = 'upload';
    mockUpload.selectedFile = null;
    render(<ISOImportModal {...defaultProps()} />);
    const btn = screen.getByText('Upload & Scan').closest('button');
    expect(btn).toBeDisabled();
  });

  it('enables Upload & Scan when a file is selected', () => {
    mockUpload.step = 'input';
    mockUpload.inputMode = 'upload';
    mockUpload.selectedFile = new File(['data'], 'test.iso');
    render(<ISOImportModal {...defaultProps()} />);
    const btn = screen.getByText('Upload & Scan').closest('button');
    expect(btn).not.toBeDisabled();
  });

  it('calls handleUpload when Upload & Scan is clicked', async () => {
    const user = userEvent.setup();
    mockUpload.step = 'input';
    mockUpload.inputMode = 'upload';
    mockUpload.selectedFile = new File(['data'], 'test.iso');
    render(<ISOImportModal {...defaultProps()} />);

    await user.click(screen.getByText('Upload & Scan'));
    expect(mockUpload.handleUpload).toHaveBeenCalledTimes(1);
  });

  // ── Step: uploading ──

  it('renders UploadProgressStep when step is uploading', () => {
    mockUpload.step = 'uploading';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByTestId('upload-progress-step')).toBeInTheDocument();
  });

  // ── Step: scanning ──

  it('renders scanning indicator when step is scanning', () => {
    mockUpload.step = 'scanning';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Scanning ISO...')).toBeInTheDocument();
    expect(screen.getByText('Parsing node definitions and images')).toBeInTheDocument();
  });

  // ── Step: review ──

  it('renders ISOReviewStep when step is review with scanResult', () => {
    mockUpload.step = 'review';
    mockUpload.scanResult = {
      session_id: 's1',
      iso_path: '/uploads/test.iso',
      format: 'cml2',
      size_bytes: 5000000000,
      node_definitions: [],
      images: [],
      parse_errors: [],
    };
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByTestId('iso-review-step')).toBeInTheDocument();
  });

  it('shows Back and Import buttons in review step', () => {
    mockUpload.step = 'review';
    mockUpload.scanResult = {
      session_id: 's1',
      iso_path: '/test.iso',
      format: 'cml2',
      size_bytes: 1000,
      node_definitions: [],
      images: [],
      parse_errors: [],
    };
    mockUpload.selectedImages = new Set(['img-1']);
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Back')).toBeInTheDocument();
    expect(screen.getByText('Import 1 Image')).toBeInTheDocument();
  });

  it('shows plural Import button text for multiple images', () => {
    mockUpload.step = 'review';
    mockUpload.scanResult = {
      session_id: 's1',
      iso_path: '/test.iso',
      format: 'cml2',
      size_bytes: 1000,
      node_definitions: [],
      images: [],
      parse_errors: [],
    };
    mockUpload.selectedImages = new Set(['img-1', 'img-2', 'img-3']);
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Import 3 Images')).toBeInTheDocument();
  });

  it('disables Import button when no images selected', () => {
    mockUpload.step = 'review';
    mockUpload.scanResult = {
      session_id: 's1',
      iso_path: '/test.iso',
      format: 'cml2',
      size_bytes: 1000,
      node_definitions: [],
      images: [],
      parse_errors: [],
    };
    mockUpload.selectedImages = new Set();
    render(<ISOImportModal {...defaultProps()} />);
    const importBtn = screen.getByText(/Import 0 Image/).closest('button');
    expect(importBtn).toBeDisabled();
  });

  it('calls setStep("input") when Back button is clicked', async () => {
    const user = userEvent.setup();
    mockUpload.step = 'review';
    mockUpload.scanResult = {
      session_id: 's1',
      iso_path: '/test.iso',
      format: 'cml2',
      size_bytes: 1000,
      node_definitions: [],
      images: [],
      parse_errors: [],
    };
    render(<ISOImportModal {...defaultProps()} />);

    await user.click(screen.getByText('Back'));
    expect(mockUpload.setStep).toHaveBeenCalledWith('input');
  });

  it('calls handleImport with onImportComplete when Import is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    mockUpload.step = 'review';
    mockUpload.scanResult = {
      session_id: 's1',
      iso_path: '/test.iso',
      format: 'cml2',
      size_bytes: 1000,
      node_definitions: [],
      images: [],
      parse_errors: [],
    };
    mockUpload.selectedImages = new Set(['img-1']);
    render(<ISOImportModal {...props} />);

    await user.click(screen.getByText('Import 1 Image'));
    expect(mockUpload.handleImport).toHaveBeenCalledWith(props.onImportComplete);
  });

  // ── Step: importing ──

  it('renders ISOImportProgress when step is importing', () => {
    mockUpload.step = 'importing';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByTestId('iso-import-progress')).toBeInTheDocument();
  });

  // ── Step: complete ──

  it('renders completion screen when step is complete', () => {
    mockUpload.step = 'complete';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Import Complete!')).toBeInTheDocument();
    expect(screen.getByText(/Images have been imported and are ready to use/)).toBeInTheDocument();
  });

  it('shows Done button when step is complete', () => {
    mockUpload.step = 'complete';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Done')).toBeInTheDocument();
  });

  it('calls onClose when Done button is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    mockUpload.step = 'complete';
    render(<ISOImportModal {...props} />);

    await user.click(screen.getByText('Done'));
    expect(props.onClose).toHaveBeenCalled();
  });

  // ── Footer Cancel/Close ──

  it('shows Cancel in footer during input step', () => {
    mockUpload.step = 'input';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('shows Close in footer during complete step', () => {
    mockUpload.step = 'complete';
    render(<ISOImportModal {...defaultProps()} />);
    expect(screen.getByText('Close')).toBeInTheDocument();
  });

  it('calls onClose when Cancel is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    mockUpload.step = 'input';
    render(<ISOImportModal {...props} />);

    await user.click(screen.getByText('Cancel'));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  // ── Lifecycle: resetState + fetchAvailableISOs on open ──

  it('calls resetState and fetchAvailableISOs when modal opens', () => {
    render(<ISOImportModal {...defaultProps()} isOpen={true} />);
    expect(mockUpload.resetState).toHaveBeenCalledTimes(1);
    expect(mockUpload.fetchAvailableISOs).toHaveBeenCalledTimes(1);
  });
});
