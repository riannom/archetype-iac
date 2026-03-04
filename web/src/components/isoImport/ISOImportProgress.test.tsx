import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { ISOImportProgress, UploadProgressStep } from './ISOImportProgress';
import { formatBytes } from './types';
import type { ImageProgress } from './types';

// ============================================================================
// formatBytes utility
// ============================================================================

describe('formatBytes', () => {
  it('returns "0 B" for zero bytes', () => {
    expect(formatBytes(0)).toBe('0 B');
  });

  it('formats bytes correctly', () => {
    expect(formatBytes(512)).toBe('512 B');
  });

  it('formats kilobytes correctly', () => {
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
  });

  it('formats megabytes correctly', () => {
    expect(formatBytes(1048576)).toBe('1 MB');
    expect(formatBytes(10485760)).toBe('10 MB');
  });

  it('formats gigabytes correctly', () => {
    expect(formatBytes(1073741824)).toBe('1 GB');
    expect(formatBytes(5368709120)).toBe('5 GB');
  });

  it('formats terabytes correctly', () => {
    expect(formatBytes(1099511627776)).toBe('1 TB');
  });
});

// ============================================================================
// Helpers
// ============================================================================

function makeProgress(overrides: Partial<ImageProgress> = {}): ImageProgress {
  return {
    image_id: 'img-001',
    status: 'extracting',
    progress_percent: 50,
    ...overrides,
  };
}

// ============================================================================
// ISOImportProgress
// ============================================================================

describe('ISOImportProgress', () => {
  it('renders overall progress bar with percentage', () => {
    render(
      <ISOImportProgress overallProgress={42} importProgress={{}} />
    );

    expect(screen.getByText('Overall Progress')).toBeInTheDocument();
    expect(screen.getByText('42%')).toBeInTheDocument();
  });

  it('renders informational header text', () => {
    render(
      <ISOImportProgress overallProgress={0} importProgress={{}} />
    );

    expect(screen.getByText('Importing Images...')).toBeInTheDocument();
    expect(
      screen.getByText(/Please don't close this window/)
    ).toBeInTheDocument();
  });

  it('renders per-image progress entries', () => {
    const progress: Record<string, ImageProgress> = {
      'csr1000v-17.03': makeProgress({
        image_id: 'csr1000v-17.03',
        status: 'extracting',
        progress_percent: 65,
      }),
      'iosv-15.9': makeProgress({
        image_id: 'iosv-15.9',
        status: 'pending',
        progress_percent: 0,
      }),
    };

    render(
      <ISOImportProgress overallProgress={30} importProgress={progress} />
    );

    expect(screen.getByText('csr1000v-17.03')).toBeInTheDocument();
    expect(screen.getByText('iosv-15.9')).toBeInTheDocument();
    expect(screen.getByText('extracting')).toBeInTheDocument();
    expect(screen.getByText('pending')).toBeInTheDocument();
  });

  it('shows completed status with emerald styling', () => {
    const progress: Record<string, ImageProgress> = {
      'done-image': makeProgress({
        status: 'completed',
        progress_percent: 100,
      }),
    };

    const { container } = render(
      <ISOImportProgress overallProgress={100} importProgress={progress} />
    );

    const statusSpan = screen.getByText('completed');
    expect(statusSpan.className).toContain('text-emerald-500');
  });

  it('shows failed status with red styling and error message', () => {
    const progress: Record<string, ImageProgress> = {
      'bad-image': makeProgress({
        status: 'failed',
        progress_percent: 30,
        error_message: 'Disk full',
      }),
    };

    render(
      <ISOImportProgress overallProgress={15} importProgress={progress} />
    );

    const statusSpan = screen.getByText('failed');
    expect(statusSpan.className).toContain('text-red-500');
    expect(screen.getByText('Disk full')).toBeInTheDocument();
  });

  it('does not render error message when absent', () => {
    const progress: Record<string, ImageProgress> = {
      'ok-image': makeProgress({
        status: 'extracting',
        progress_percent: 50,
        error_message: undefined,
      }),
    };

    render(
      <ISOImportProgress overallProgress={50} importProgress={progress} />
    );

    // Only error_message text would appear; since none, just confirm no red error text
    const container = screen.getByText('extracting').closest('div')!.parentElement!;
    expect(container.querySelectorAll('.text-red-500').length).toBe(0);
  });

  it('shows spinner icon for extracting status', () => {
    const progress: Record<string, ImageProgress> = {
      'ext-image': makeProgress({ status: 'extracting', progress_percent: 40 }),
    };

    const { container } = render(
      <ISOImportProgress overallProgress={40} importProgress={progress} />
    );

    const spinner = container.querySelector('i.fa-spinner.fa-spin');
    expect(spinner).toBeInTheDocument();
  });
});

// ============================================================================
// UploadProgressStep
// ============================================================================

describe('UploadProgressStep', () => {
  function defaultProps() {
    return {
      uploadProgress: 0,
      uploadStatus: '',
      selectedFile: null as File | null,
      cancelUpload: vi.fn(),
    };
  }

  it('renders upload header and progress', () => {
    render(<UploadProgressStep {...defaultProps()} uploadProgress={55} />);

    expect(screen.getByText('Uploading ISO...')).toBeInTheDocument();
    expect(screen.getByText('Upload Progress')).toBeInTheDocument();
    expect(screen.getByText('55%')).toBeInTheDocument();
  });

  it('shows upload status message', () => {
    render(
      <UploadProgressStep
        {...defaultProps()}
        uploadStatus="Sending chunk 3/10..."
      />
    );

    expect(screen.getByText('Sending chunk 3/10...')).toBeInTheDocument();
  });

  it('shows fallback status when uploadStatus is empty', () => {
    render(<UploadProgressStep {...defaultProps()} />);

    expect(screen.getByText('Preparing upload...')).toBeInTheDocument();
  });

  it('renders file size info when selectedFile is provided', () => {
    const file = new File(['x'.repeat(1024 * 1024)], 'test.iso', {
      type: 'application/octet-stream',
    });
    // File.size is read-only, so we use a real file with known size
    // 1MB file at 50% progress

    render(
      <UploadProgressStep
        {...defaultProps()}
        uploadProgress={50}
        selectedFile={file}
      />
    );

    // Should show bytes transferred / total bytes
    const sizeText = screen.getByText(/\//);
    expect(sizeText).toBeInTheDocument();
  });

  it('does not render file size when selectedFile is null', () => {
    const { container } = render(
      <UploadProgressStep {...defaultProps()} uploadProgress={50} />
    );

    // The size display paragraph only renders when selectedFile is truthy
    const sizeElements = container.querySelectorAll('.text-\\[10px\\].text-stone-400');
    expect(sizeElements.length).toBe(0);
  });

  it('calls cancelUpload when cancel button is clicked', () => {
    const cancelUpload = vi.fn();
    render(
      <UploadProgressStep {...defaultProps()} cancelUpload={cancelUpload} />
    );

    fireEvent.click(screen.getByText('Cancel Upload'));
    expect(cancelUpload).toHaveBeenCalledOnce();
  });
});
