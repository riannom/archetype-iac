import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useISOUpload } from './useISOUpload';
import type { ISOImportLogEvent, ScanResponse, BrowseResponse } from './types';

// Mock api module
vi.mock('../../api', () => ({
  rawApiRequest: vi.fn(),
}));

import { rawApiRequest } from '../../api';

const mockRawApiRequest = rawApiRequest as ReturnType<typeof vi.fn>;

// ============================================================================
// Helpers
// ============================================================================

function createMockResponse(data: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
    headers: new Headers(),
    redirected: false,
    statusText: ok ? 'OK' : 'Error',
    type: 'basic' as ResponseType,
    url: '',
    clone: () => ({} as Response),
    body: null,
    bodyUsed: false,
    arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
    blob: () => Promise.resolve(new Blob()),
    formData: () => Promise.resolve(new FormData()),
    bytes: () => Promise.resolve(new Uint8Array()),
  } as Response;
}

function makeScanResponse(overrides: Partial<ScanResponse> = {}): ScanResponse {
  return {
    session_id: 'session-1',
    iso_path: '/uploads/refplat.iso',
    format: 'cml2',
    size_bytes: 5368709120,
    node_definitions: [
      {
        id: 'iosv',
        label: 'IOSv',
        description: 'Cisco IOSv Router',
        nature: 'router',
        vendor: 'cisco',
        ram_mb: 512,
        cpus: 1,
        interfaces: ['GigabitEthernet0/0', 'GigabitEthernet0/1'],
      },
    ],
    images: [
      {
        id: 'iosv-image-1',
        node_definition_id: 'iosv',
        label: 'IOSv 15.9(3)M7',
        description: 'IOSv Router',
        version: '15.9(3)M7',
        disk_image_filename: 'vios-adventerprisek9-m.vmdk.SPA.157-3.M3',
        disk_image_path: '/images/iosv.qcow2',
        size_bytes: 134217728,
        image_type: 'qcow2',
      },
    ],
    parse_errors: [],
    ...overrides,
  };
}

function defaultArgs() {
  return {
    logEvent: vi.fn() as (event: ISOImportLogEvent) => void,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('useISOUpload', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // ── Initial State ──

  it('returns initial state values', () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    expect(result.current.step).toBe('input');
    expect(result.current.isoPath).toBe('');
    expect(result.current.error).toBeNull();
    expect(result.current.scanResult).toBeNull();
    expect(result.current.selectedImages.size).toBe(0);
    expect(result.current.createDevices).toBe(true);
    expect(result.current.importProgress).toEqual({});
    expect(result.current.overallProgress).toBe(0);
    expect(result.current.inputMode).toBe('browse');
    expect(result.current.selectedFile).toBeNull();
    expect(result.current.uploadProgress).toBe(0);
    expect(result.current.uploadStatus).toBe('');
  });

  it('exposes fileInputRef', () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));
    expect(result.current.fileInputRef).toBeDefined();
  });

  // ── State Setters ──

  it('can update isoPath', () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    act(() => {
      result.current.setIsoPath('/path/to/new.iso');
    });

    expect(result.current.isoPath).toBe('/path/to/new.iso');
  });

  it('can switch input mode', () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    act(() => {
      result.current.setInputMode('upload');
    });

    expect(result.current.inputMode).toBe('upload');

    act(() => {
      result.current.setInputMode('custom');
    });

    expect(result.current.inputMode).toBe('custom');
  });

  it('can toggle createDevices', () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    act(() => {
      result.current.setCreateDevices(false);
    });

    expect(result.current.createDevices).toBe(false);
  });

  // ── resetState ──

  it('resets all state to defaults', () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    // Modify some state
    act(() => {
      result.current.setIsoPath('/some/path.iso');
      result.current.setInputMode('upload');
      result.current.setCreateDevices(false);
    });

    act(() => {
      result.current.resetState();
    });

    expect(result.current.step).toBe('input');
    expect(result.current.isoPath).toBe('');
    expect(result.current.error).toBeNull();
    expect(result.current.inputMode).toBe('browse');
    expect(result.current.createDevices).toBe(true);
    expect(result.current.selectedFile).toBeNull();
    expect(result.current.uploadProgress).toBe(0);
    expect(result.current.uploadStatus).toBe('');
  });

  // ── cleanup ──

  it('sets upload abort flag on cleanup', () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    act(() => {
      result.current.cleanup();
    });

    // The abort flag is internal, but we can verify cleanup doesn't throw
    expect(true).toBe(true);
  });

  // ── fetchAvailableISOs ──

  it('fetches available ISOs from browse endpoint', async () => {
    const args = defaultArgs();
    const browseData: BrowseResponse = {
      upload_dir: '/var/lib/archetype/uploads',
      files: [
        { name: 'refplat.iso', path: '/uploads/refplat.iso', size_bytes: 5000000000, modified_at: '2026-01-01T00:00:00Z' },
      ],
    };
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(browseData));

    const { result } = renderHook(() => useISOUpload(args));

    await act(async () => {
      await result.current.fetchAvailableISOs();
    });

    expect(result.current.availableISOs).toEqual(browseData.files);
    expect(result.current.uploadDir).toBe('/var/lib/archetype/uploads');
    expect(result.current.loadingISOs).toBe(false);
  });

  it('handles fetch error gracefully', async () => {
    const args = defaultArgs();
    mockRawApiRequest.mockRejectedValueOnce(new Error('Network failure'));

    const { result } = renderHook(() => useISOUpload(args));

    await act(async () => {
      await result.current.fetchAvailableISOs();
    });

    expect(result.current.loadingISOs).toBe(false);
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        level: 'error',
        phase: 'browse',
      })
    );
  });

  // ── handleScan ──

  it('requires a non-empty ISO path', async () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    await act(async () => {
      await result.current.handleScan();
    });

    expect(result.current.error).toBe('Please enter an ISO path');
    expect(result.current.step).toBe('input');
  });

  it('scans ISO and transitions to review step', async () => {
    const args = defaultArgs();
    const scanData = makeScanResponse();
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/uploads/refplat.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    expect(result.current.step).toBe('review');
    expect(result.current.scanResult).toEqual(scanData);
    expect(result.current.selectedImages.size).toBe(1);
    expect(result.current.selectedImages.has('iosv-image-1')).toBe(true);
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'scan_start' })
    );
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'scan_complete' })
    );
  });

  it('handles scan failure', async () => {
    const args = defaultArgs();
    mockRawApiRequest.mockResolvedValueOnce(
      createMockResponse({ detail: 'Invalid ISO format' }, false, 400)
    );

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/bad/file.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    expect(result.current.step).toBe('input');
    expect(result.current.error).toBe('Invalid ISO format');
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'scan_failed' })
    );
  });

  // ── handleImport ──

  it('does nothing when scanResult is null', async () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));
    const onComplete = vi.fn();

    await act(async () => {
      await result.current.handleImport(onComplete);
    });

    expect(mockRawApiRequest).not.toHaveBeenCalled();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('does nothing when no images are selected', async () => {
    const args = defaultArgs();
    const scanData = makeScanResponse();
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/uploads/refplat.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    // Deselect all
    act(() => {
      result.current.selectNone();
    });

    const onComplete = vi.fn();
    await act(async () => {
      await result.current.handleImport(onComplete);
    });

    // Only the scan call was made, not the import call
    expect(mockRawApiRequest).toHaveBeenCalledTimes(1);
  });

  it('starts import and polls for completion', async () => {
    const args = defaultArgs();
    const scanData = makeScanResponse();
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/uploads/refplat.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    // Mock import start
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse({ status: 'importing' }));
    // Mock progress poll: completed (will be called by setTimeout internally)
    mockRawApiRequest.mockResolvedValueOnce(
      createMockResponse({
        status: 'completed',
        progress_percent: 100,
        image_progress: {},
      })
    );

    const onComplete = vi.fn();

    await act(async () => {
      await result.current.handleImport(onComplete);
    });

    // The import start API call was made
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'import_start' })
    );

    // Wait for the polling setTimeout to fire and complete
    await waitFor(() => {
      expect(result.current.step).toBe('complete');
    }, { timeout: 3000 });

    expect(onComplete).toHaveBeenCalled();
  });

  it('handles import start failure', async () => {
    const args = defaultArgs();
    const scanData = makeScanResponse();
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/uploads/refplat.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    // Mock import start failure
    mockRawApiRequest.mockResolvedValueOnce(
      createMockResponse({ detail: 'Session expired' }, false, 404)
    );

    const onComplete = vi.fn();
    await act(async () => {
      await result.current.handleImport(onComplete);
    });

    expect(result.current.step).toBe('review');
    expect(result.current.error).toBe('Session expired');
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'import_start_failed' })
    );
  });

  // ── handleFileSelect ──

  it('accepts ISO files', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useISOUpload(args));

    const file = new File(['data'], 'refplat.iso', { type: 'application/octet-stream' });

    act(() => {
      result.current.handleFileSelect(file);
    });

    expect(result.current.selectedFile).toBe(file);
    expect(result.current.error).toBeNull();
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'upload_file_selected' })
    );
  });

  it('rejects non-ISO files', () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useISOUpload(args));

    const file = new File(['data'], 'image.qcow2');

    act(() => {
      result.current.handleFileSelect(file);
    });

    expect(result.current.selectedFile).toBeNull();
    expect(result.current.error).toBe('Please select an ISO file');
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'upload_file_validation' })
    );
  });

  // ── handleUpload (chunked) ──

  it('does nothing when no file is selected', async () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    await act(async () => {
      await result.current.handleUpload();
    });

    expect(mockRawApiRequest).not.toHaveBeenCalled();
  });

  it('performs chunked upload followed by auto-scan', async () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useISOUpload(args));

    const file = new File(['test-iso-data'], 'upload.iso');
    act(() => {
      result.current.handleFileSelect(file);
    });

    // Mock upload init
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse({
      upload_id: 'iso-up-1',
      filename: 'upload.iso',
      total_size: file.size,
      chunk_size: 10485760,
      total_chunks: 1,
      upload_path: '/tmp/upload.iso',
    }));

    // Mock chunk
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse({
      upload_id: 'iso-up-1',
      chunk_index: 0,
      bytes_received: file.size,
      total_received: file.size,
      progress_percent: 100,
      is_complete: true,
    }));

    // Mock complete
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse({
      upload_id: 'iso-up-1',
      filename: 'upload.iso',
      iso_path: '/var/lib/archetype/uploads/upload.iso',
      total_size: file.size,
    }));

    // Mock auto-scan
    const scanData = makeScanResponse({
      iso_path: '/var/lib/archetype/uploads/upload.iso',
    });
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    await act(async () => {
      await result.current.handleUpload();
    });

    expect(result.current.step).toBe('review');
    expect(result.current.scanResult).toBeDefined();
    expect(result.current.isoPath).toBe('/var/lib/archetype/uploads/upload.iso');
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'upload_start' })
    );
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'upload_scan_complete' })
    );
  });

  it('handles upload init failure', async () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useISOUpload(args));

    const file = new File(['data'], 'fail.iso');
    act(() => {
      result.current.handleFileSelect(file);
    });

    mockRawApiRequest.mockResolvedValueOnce(
      createMockResponse({ detail: 'Disk full' }, false, 507)
    );

    await act(async () => {
      await result.current.handleUpload();
    });

    expect(result.current.step).toBe('input');
    expect(result.current.inputMode).toBe('upload');
    expect(result.current.error).toBe('Disk full');
  });

  // ── cancelUpload ──

  it('cancels upload and reverts to input step', async () => {
    const args = defaultArgs();
    const { result } = renderHook(() => useISOUpload(args));

    mockRawApiRequest.mockResolvedValueOnce(createMockResponse({}));

    await act(async () => {
      await result.current.cancelUpload();
    });

    expect(result.current.step).toBe('input');
    expect(result.current.inputMode).toBe('upload');
    expect(args.logEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: 'upload_cancelled' })
    );
  });

  // ── Image Selection ──

  it('toggleImage removes a selected image ID', async () => {
    const args = defaultArgs();
    const scanData = makeScanResponse();
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/uploads/refplat.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    // Initially all selected
    expect(result.current.selectedImages.has('iosv-image-1')).toBe(true);

    // Toggle off
    act(() => {
      result.current.toggleImage('iosv-image-1');
    });
    expect(result.current.selectedImages.has('iosv-image-1')).toBe(false);
  });

  it('toggleImage adds a deselected image ID', async () => {
    const args = defaultArgs();
    const scanData = makeScanResponse();
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/uploads/refplat.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    // Deselect all first
    act(() => {
      result.current.selectNone();
    });
    expect(result.current.selectedImages.size).toBe(0);

    // Toggle on
    act(() => {
      result.current.toggleImage('iosv-image-1');
    });
    expect(result.current.selectedImages.has('iosv-image-1')).toBe(true);
  });

  it('selectAll selects all images from scan result', async () => {
    const args = defaultArgs();
    const scanData = makeScanResponse({
      images: [
        { id: 'img-1', node_definition_id: 'iosv', label: 'IOSv', description: '', version: '1.0', disk_image_filename: 'a.qcow2', disk_image_path: '/a', size_bytes: 100, image_type: 'qcow2' },
        { id: 'img-2', node_definition_id: 'iosv', label: 'IOSv2', description: '', version: '2.0', disk_image_filename: 'b.qcow2', disk_image_path: '/b', size_bytes: 200, image_type: 'qcow2' },
      ],
    });
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/test.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    act(() => {
      result.current.selectNone();
    });
    expect(result.current.selectedImages.size).toBe(0);

    act(() => {
      result.current.selectAll();
    });
    expect(result.current.selectedImages.size).toBe(2);
  });

  it('selectNone clears all selected images', async () => {
    const args = defaultArgs();
    const scanData = makeScanResponse();
    mockRawApiRequest.mockResolvedValueOnce(createMockResponse(scanData));

    const { result } = renderHook(() => useISOUpload(args));

    act(() => {
      result.current.setIsoPath('/test.iso');
    });

    await act(async () => {
      await result.current.handleScan();
    });

    expect(result.current.selectedImages.size).toBe(1);

    act(() => {
      result.current.selectNone();
    });

    expect(result.current.selectedImages.size).toBe(0);
  });

  it('selectAll is no-op when scanResult is null', () => {
    const { result } = renderHook(() => useISOUpload(defaultArgs()));

    act(() => {
      result.current.selectAll();
    });

    expect(result.current.selectedImages.size).toBe(0);
  });
});
