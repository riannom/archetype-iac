import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import UploadControls from './UploadControls';
import type { Qcow2ConfirmState } from './deviceManagerTypes';

// Mock ISOImportModal since it fetches data and has complex internals
vi.mock('../../../components/ISOImportModal', () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) =>
    isOpen ? (
      <div data-testid="iso-import-modal">
        <button onClick={onClose}>Close ISO Modal</button>
      </div>
    ) : null,
  ISOImportModal: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) =>
    isOpen ? (
      <div data-testid="iso-import-modal">
        <button onClick={onClose}>Close ISO Modal</button>
      </div>
    ) : null,
}));

// Mock Modal component
vi.mock('../../../components/ui/Modal', () => ({
  Modal: ({ isOpen, onClose, title, children }: { isOpen: boolean; onClose: () => void; title: string; children: React.ReactNode }) =>
    isOpen ? (
      <div data-testid="modal" role="dialog" aria-label={title}>
        <h2>{title}</h2>
        <button onClick={onClose} data-testid="modal-close">Close</button>
        {children}
      </div>
    ) : null,
}));

const defaultProps = () => ({
  uploadStatus: null as string | null,
  uploadProgress: null as number | null,
  qcow2Progress: null as number | null,
  isQcow2PostProcessing: false,
  uploadErrorCount: 0,
  fileInputRef: { current: null } as React.MutableRefObject<HTMLInputElement | null>,
  qcow2InputRef: { current: null } as React.MutableRefObject<HTMLInputElement | null>,
  showISOModal: false,
  setShowISOModal: vi.fn(),
  qcow2Confirm: null as Qcow2ConfirmState | null,
  setQcow2Confirm: vi.fn(),
  openFilePicker: vi.fn(),
  openQcow2Picker: vi.fn(),
  uploadImage: vi.fn(),
  uploadQcow2: vi.fn(),
  confirmQcow2Upload: vi.fn(),
  cancelQcow2Confirm: vi.fn(),
  handleIsoLogEvent: vi.fn(),
  onRefresh: vi.fn(),
  onShowUploadLogs: vi.fn(),
});

describe('UploadControls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Header Rendering ──

  it('renders the Image Management heading', () => {
    render(<UploadControls {...defaultProps()} />);
    expect(screen.getByText('Image Management')).toBeInTheDocument();
  });

  it('renders all three upload buttons', () => {
    render(<UploadControls {...defaultProps()} />);
    expect(screen.getByText(/Upload Docker/)).toBeInTheDocument();
    expect(screen.getByText(/Upload QCOW2/)).toBeInTheDocument();
    expect(screen.getByText(/Import ISO/)).toBeInTheDocument();
  });

  it('renders Logs button', () => {
    render(<UploadControls {...defaultProps()} />);
    expect(screen.getByText(/Logs/)).toBeInTheDocument();
  });

  // ── File Picker Actions ──

  it('calls openFilePicker when Upload Docker is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<UploadControls {...props} />);
    await user.click(screen.getByText(/Upload Docker/).closest('button')!);
    expect(props.openFilePicker).toHaveBeenCalledTimes(1);
  });

  it('calls openQcow2Picker when Upload QCOW2 is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<UploadControls {...props} />);
    await user.click(screen.getByText(/Upload QCOW2/).closest('button')!);
    expect(props.openQcow2Picker).toHaveBeenCalledTimes(1);
  });

  it('calls setShowISOModal when Import ISO is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<UploadControls {...props} />);
    await user.click(screen.getByText(/Import ISO/).closest('button')!);
    expect(props.setShowISOModal).toHaveBeenCalledWith(true);
  });

  it('calls onShowUploadLogs when Logs is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<UploadControls {...props} />);
    await user.click(screen.getByText(/Logs/).closest('button')!);
    expect(props.onShowUploadLogs).toHaveBeenCalledTimes(1);
  });

  // ── Upload Status & Progress ──

  it('shows upload status text when set', () => {
    const props = defaultProps();
    props.uploadStatus = 'Uploading ceos:4.28.0F...';
    render(<UploadControls {...props} />);
    expect(screen.getByText('Uploading ceos:4.28.0F...')).toBeInTheDocument();
  });

  it('shows Docker upload progress bar', () => {
    const props = defaultProps();
    props.uploadProgress = 65;
    const { container } = render(<UploadControls {...props} />);
    expect(screen.getByText('Image upload 65%')).toBeInTheDocument();
    const bar = container.querySelector('[style*="width: 65%"]');
    expect(bar).toBeInTheDocument();
  });

  it('shows QCOW2 upload progress bar', () => {
    const props = defaultProps();
    props.qcow2Progress = 42;
    const { container } = render(<UploadControls {...props} />);
    expect(screen.getByText('QCOW2 upload 42%')).toBeInTheDocument();
    const bar = container.querySelector('[style*="width: 42%"]');
    expect(bar).toBeInTheDocument();
  });

  it('shows QCOW2 post-processing state', () => {
    const props = defaultProps();
    props.qcow2Progress = 100;
    props.isQcow2PostProcessing = true;
    render(<UploadControls {...props} />);
    expect(screen.getByText('QCOW2 upload complete. Processing image...')).toBeInTheDocument();
  });

  // ── Error Badge ──

  it('shows error count badge on Logs button when errors exist', () => {
    const props = defaultProps();
    props.uploadErrorCount = 3;
    render(<UploadControls {...props} />);
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('does not show error badge when uploadErrorCount is 0', () => {
    const props = defaultProps();
    props.uploadErrorCount = 0;
    const { container } = render(<UploadControls {...props} />);
    const badge = container.querySelector('.bg-red-100');
    expect(badge).not.toBeInTheDocument();
  });

  // ── QCOW2 Confirm Modal ──

  it('shows QCOW2 confirmation modal when qcow2Confirm is set', () => {
    const props = defaultProps();
    props.qcow2Confirm = {
      uploadId: 'upload-1',
      filename: 'nexus9500v.10.3.1.qcow2',
      detection: {
        detected_device_id: 'cisco_n9kv',
        detected_version: '10.3.1',
        confidence: 'high',
        size_bytes: 2147483648,
        sha256: null,
        suggested_metadata: { memory: 12288, cpu: 2 },
      },
      deviceIdOverride: 'cisco_n9kv',
      versionOverride: '10.3.1',
      autoBuild: true,
    };
    render(<UploadControls {...props} />);
    expect(screen.getByText('Confirm QCOW2 Image')).toBeInTheDocument();
    expect(screen.getByText('nexus9500v.10.3.1.qcow2')).toBeInTheDocument();
    expect(screen.getByText('Detection confidence: high')).toBeInTheDocument();
  });

  it('calls confirmQcow2Upload when Confirm Import is clicked', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.qcow2Confirm = {
      uploadId: 'upload-1',
      filename: 'test.qcow2',
      detection: {
        detected_device_id: 'test',
        detected_version: '1.0',
        confidence: 'high',
        size_bytes: null,
        sha256: null,
        suggested_metadata: {},
      },
      deviceIdOverride: 'test',
      versionOverride: '1.0',
      autoBuild: false,
    };
    render(<UploadControls {...props} />);
    await user.click(screen.getByText('Confirm Import'));
    expect(props.confirmQcow2Upload).toHaveBeenCalledTimes(1);
  });

  it('calls cancelQcow2Confirm when Cancel is clicked in confirm modal', async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.qcow2Confirm = {
      uploadId: 'upload-1',
      filename: 'test.qcow2',
      detection: {
        detected_device_id: null,
        detected_version: null,
        confidence: 'none',
        size_bytes: null,
        sha256: null,
        suggested_metadata: {},
      },
      deviceIdOverride: '',
      versionOverride: '',
      autoBuild: false,
    };
    render(<UploadControls {...props} />);
    // Click the Cancel button within the QCOW2 confirm modal
    await user.click(screen.getByText('Cancel'));
    expect(props.cancelQcow2Confirm).toHaveBeenCalledTimes(1);
  });

  // ── Hidden File Inputs ──

  it('renders hidden Docker file input with correct accept types', () => {
    const { container } = render(<UploadControls {...defaultProps()} />);
    const dockerInput = container.querySelector('input[accept=".tar,.tgz,.tar.gz,.tar.xz,.txz"]');
    expect(dockerInput).toBeInTheDocument();
    expect(dockerInput).toHaveClass('hidden');
  });

  it('renders hidden QCOW2 file input with correct accept types', () => {
    const { container } = render(<UploadControls {...defaultProps()} />);
    const qcow2Input = container.querySelector('input[accept=".qcow2,.qcow,.img,.qcow2.gz,.qcow.gz,.img.gz"]');
    expect(qcow2Input).toBeInTheDocument();
    expect(qcow2Input).toHaveClass('hidden');
  });
});
