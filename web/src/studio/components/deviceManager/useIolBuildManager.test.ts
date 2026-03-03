import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useIolBuildManager } from './useIolBuildManager';
import type { ImageLibraryEntry } from '../../types';

// Mock api module
vi.mock('../../../api', () => ({
  apiRequest: vi.fn(),
}));

// Mock usePersistedState to use plain useState (avoids localStorage side effects)
vi.mock('../../hooks/usePersistedState', async () => {
  const react = await import('react');
  return {
    usePersistedState: <T,>(_key: string, defaultValue: T): [T, (v: T | ((p: T) => T)) => void] => {
      return react.useState<T>(defaultValue);
    },
  };
});

// Mock usePolling to be a no-op (we test behavior directly)
vi.mock('../../hooks/usePolling', () => ({
  usePolling: vi.fn(),
}));

import { apiRequest } from '../../../api';

const mockApiRequest = apiRequest as ReturnType<typeof vi.fn>;

// ============================================================================
// Helpers - stable references to avoid infinite renders
// ============================================================================

function makeIolImage(overrides: Partial<ImageLibraryEntry> = {}): ImageLibraryEntry {
  return {
    id: 'iol-img-1',
    kind: 'iol',
    reference: 'iou-l3-adventerprisek9-15.5.2T.bin',
    filename: 'iou-l3-adventerprisek9-15.5.2T.bin',
    ...overrides,
  };
}

function makeDockerImage(overrides: Partial<ImageLibraryEntry> = {}): ImageLibraryEntry {
  return {
    id: 'docker-built-1',
    kind: 'docker',
    reference: 'archetype/iol:latest',
    built_from: 'iol-img-1',
    ...overrides,
  };
}

// Stable references: vi.fn() created once at module level, cleared in beforeEach
const stableOnRefresh = vi.fn();
const stableSetUploadStatus = vi.fn();

// ============================================================================
// Tests
// ============================================================================

describe('useIolBuildManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Helper that uses stable references
  function renderManager(imageLibrary: ImageLibraryEntry[] = [], isBuildJobsMode = false) {
    // Create a stable array reference for the library
    const lib = imageLibrary;
    return renderHook(() => useIolBuildManager({
      imageLibrary: lib,
      isBuildJobsMode,
      onRefresh: stableOnRefresh,
      setUploadStatus: stableSetUploadStatus,
    }));
  }

  // ── Initial State ──

  it('returns empty build rows when no IOL images exist', () => {
    const { result } = renderManager();

    expect(result.current.iolBuildRows).toEqual([]);
    expect(result.current.hasActiveIolBuilds).toBe(false);
    expect(result.current.activeIolBuildCount).toBe(0);
    expect(result.current.currentIolBuildRows).toEqual([]);
    expect(result.current.historicalIolBuildRows).toEqual([]);
  });

  it('returns default UI state', () => {
    const { result } = renderManager();

    expect(result.current.refreshingIolBuilds).toBe(false);
    expect(result.current.retryingIolImageId).toBeNull();
    expect(result.current.ignoringIolImageId).toBeNull();
    expect(result.current.showIolDiagnostics).toBe(false);
    expect(result.current.iolDiagnostics).toBeNull();
    expect(result.current.iolDiagnosticsLoading).toBe(false);
    expect(result.current.iolDiagnosticsError).toBeNull();
    expect(result.current.autoRefreshIolBuilds).toBe(true);
  });

  // ── Build Rows Computation ──

  it('computes build rows from IOL source images with default status', () => {
    const { result } = renderManager([makeIolImage()]);

    expect(result.current.iolBuildRows.length).toBe(1);
    expect(result.current.iolBuildRows[0].image.id).toBe('iol-img-1');
    expect(result.current.iolBuildRows[0].status).toBe('not_started');
  });

  it('marks IOL images as complete when built Docker images exist', () => {
    const { result } = renderManager([makeIolImage(), makeDockerImage()]);

    expect(result.current.iolBuildRows.length).toBe(1);
    expect(result.current.iolBuildRows[0].status).toBe('complete');
  });

  it('separates current and historical build rows', () => {
    const { result } = renderManager([
      makeIolImage({ id: 'iol-1' }),
      makeIolImage({ id: 'iol-2', build_status: 'failed' }),
      makeDockerImage({ id: 'docker-1', built_from: 'iol-1' }),
    ]);

    expect(result.current.historicalIolBuildRows.length).toBe(1);
    expect(result.current.historicalIolBuildRows[0].image.id).toBe('iol-1');
    expect(result.current.currentIolBuildRows.length).toBe(1);
    expect(result.current.currentIolBuildRows[0].image.id).toBe('iol-2');
  });

  it('ignores non-IOL images in build rows', () => {
    const { result } = renderManager([
      makeIolImage(),
      { id: 'docker-standalone', kind: 'docker', reference: 'ceos:latest' } as ImageLibraryEntry,
      { id: 'qcow2-img', kind: 'qcow2', reference: 'n9kv.qcow2' } as ImageLibraryEntry,
    ]);

    expect(result.current.iolBuildRows.length).toBe(1);
  });

  it('detects active builds from image build_status', () => {
    const { result } = renderManager([
      makeIolImage({ id: 'iol-1', build_status: 'building' }),
      makeIolImage({ id: 'iol-2', build_status: 'queued' }),
    ]);

    expect(result.current.hasActiveIolBuilds).toBe(true);
    expect(result.current.activeIolBuildCount).toBe(2);
  });

  it('computes build error from image data', () => {
    const { result } = renderManager([
      makeIolImage({ build_status: 'failed', build_error: 'Docker daemon error' }),
    ]);

    expect(result.current.iolBuildRows[0].buildError).toBe('Docker daemon error');
  });

  it('computes buildJobId from image data', () => {
    const { result } = renderManager([
      makeIolImage({ build_status: 'building', build_job_id: 'rq-job-123' }),
    ]);

    expect(result.current.iolBuildRows[0].buildJobId).toBe('rq-job-123');
  });

  it('shows docker reference from built docker image', () => {
    const { result } = renderManager([
      makeIolImage(),
      makeDockerImage({ reference: 'archetype/iol:15.5.2T' }),
    ]);

    expect(result.current.iolBuildRows[0].dockerReference).toBe('archetype/iol:15.5.2T');
    expect(result.current.iolBuildRows[0].dockerImageId).toBe('docker-built-1');
  });

  it('shows ignored build details', () => {
    const { result } = renderManager([
      makeIolImage({
        build_status: 'ignored',
        build_ignored_at: '2026-01-15T10:00:00Z',
        build_ignored_by: 'admin',
      }),
    ]);

    expect(result.current.iolBuildRows[0].status).toBe('ignored');
    expect(result.current.iolBuildRows[0].buildIgnoredAt).toBe('2026-01-15T10:00:00Z');
    expect(result.current.iolBuildRows[0].buildIgnoredBy).toBe('admin');
  });

  // ── refreshIolBuildStatuses ──

  it('clears statuses when no IOL images exist', async () => {
    const { result } = renderManager();

    await act(async () => {
      await result.current.refreshIolBuildStatuses();
    });

    expect(mockApiRequest).not.toHaveBeenCalled();
  });

  it('fetches build statuses and updates rows', async () => {
    mockApiRequest.mockResolvedValueOnce({ status: 'building', build_job_id: 'job-1' });

    const { result } = renderManager([makeIolImage()]);

    await act(async () => {
      await result.current.refreshIolBuildStatuses();
    });

    expect(mockApiRequest).toHaveBeenCalledWith(
      `/images/library/${encodeURIComponent('iol-img-1')}/build-status`
    );
    expect(result.current.iolBuildRows[0].status).toBe('building');
    expect(result.current.refreshingIolBuilds).toBe(false);
  });

  it('handles API errors gracefully during refresh', async () => {
    mockApiRequest.mockRejectedValueOnce(new Error('Network error'));

    const { result } = renderManager([makeIolImage()]);

    await act(async () => {
      await result.current.refreshIolBuildStatuses();
    });

    expect(result.current.refreshingIolBuilds).toBe(false);
    expect(result.current.iolBuildRows[0].buildError).toBeTruthy();
  });

  it('calls onRefresh when newly completed builds are detected', async () => {
    // First call: building
    mockApiRequest.mockResolvedValueOnce({ status: 'building' });

    const { result } = renderManager([makeIolImage({ id: 'iol-new' })]);

    await act(async () => {
      await result.current.refreshIolBuildStatuses();
    });

    expect(stableOnRefresh).not.toHaveBeenCalled();

    // Second call: complete
    mockApiRequest.mockResolvedValueOnce({ status: 'complete' });

    await act(async () => {
      await result.current.refreshIolBuildStatuses();
    });

    expect(stableOnRefresh).toHaveBeenCalled();
  });

  // ── retryIolBuild ──

  it('retries build and refreshes statuses', async () => {
    const { result } = renderManager([makeIolImage()]);

    mockApiRequest.mockResolvedValueOnce({ build_status: 'queued', build_job_id: 'retry-1' });
    mockApiRequest.mockResolvedValueOnce({ status: 'queued' });

    await act(async () => {
      await result.current.retryIolBuild('iol-img-1', false);
    });

    expect(stableSetUploadStatus).toHaveBeenCalledWith('IOL build retry queued.');
    expect(result.current.retryingIolImageId).toBeNull();
  });

  it('handles force rebuild', async () => {
    const { result } = renderManager([makeIolImage()]);

    mockApiRequest.mockResolvedValueOnce({ build_status: 'queued', build_job_id: 'force-1' });
    mockApiRequest.mockResolvedValueOnce({ status: 'queued' });

    await act(async () => {
      await result.current.retryIolBuild('iol-img-1', true);
    });

    expect(stableSetUploadStatus).toHaveBeenCalledWith('Forced IOL rebuild queued.');
  });

  it('handles retry error', async () => {
    const { result } = renderManager([makeIolImage()]);

    mockApiRequest.mockRejectedValueOnce(new Error('Retry forbidden'));

    await act(async () => {
      await result.current.retryIolBuild('iol-img-1', false);
    });

    expect(stableSetUploadStatus).toHaveBeenCalledWith('Retry forbidden');
    expect(result.current.retryingIolImageId).toBeNull();
  });

  // ── ignoreIolBuildFailure ──

  it('ignores build failure and refreshes', async () => {
    const { result } = renderManager([makeIolImage()]);

    mockApiRequest.mockResolvedValueOnce({ build_status: 'ignored' });
    mockApiRequest.mockResolvedValueOnce({ status: 'ignored' });

    await act(async () => {
      await result.current.ignoreIolBuildFailure('iol-img-1');
    });

    expect(stableSetUploadStatus).toHaveBeenCalledWith('IOL build failure ignored.');
    expect(result.current.ignoringIolImageId).toBeNull();
  });

  it('handles ignore failure error', async () => {
    const { result } = renderManager([makeIolImage()]);

    mockApiRequest.mockRejectedValueOnce(new Error('Cannot ignore'));

    await act(async () => {
      await result.current.ignoreIolBuildFailure('iol-img-1');
    });

    expect(stableSetUploadStatus).toHaveBeenCalledWith('Cannot ignore');
    expect(result.current.ignoringIolImageId).toBeNull();
  });

  // ── openIolDiagnostics ──

  it('loads diagnostics successfully', async () => {
    const { result } = renderManager();

    const diagnosticsData = {
      image_id: 'iol-img-1',
      filename: 'test.bin',
      status: 'failed',
      build_error: 'Build timeout',
      recommended_action: 'Retry the build',
      queue_job: {
        id: 'job-1',
        status: 'failed',
        started_at: '2026-01-01T00:00:00Z',
        ended_at: '2026-01-01T00:10:00Z',
        error_log: 'Traceback: ...',
      },
    };

    mockApiRequest.mockResolvedValueOnce(diagnosticsData);

    await act(async () => {
      await result.current.openIolDiagnostics('iol-img-1');
    });

    expect(result.current.showIolDiagnostics).toBe(true);
    expect(result.current.iolDiagnosticsLoading).toBe(false);
    expect(result.current.iolDiagnostics).toEqual(diagnosticsData);
    expect(result.current.iolDiagnosticsError).toBeNull();
  });

  it('handles diagnostics load error', async () => {
    const { result } = renderManager();

    mockApiRequest.mockRejectedValueOnce(new Error('Diagnostics unavailable'));

    await act(async () => {
      await result.current.openIolDiagnostics('iol-img-1');
    });

    expect(result.current.showIolDiagnostics).toBe(true);
    expect(result.current.iolDiagnosticsLoading).toBe(false);
    expect(result.current.iolDiagnostics).toBeNull();
    expect(result.current.iolDiagnosticsError).toBe('Diagnostics unavailable');
  });

  // ── Mode switching ──

  it('does not fetch when isBuildJobsMode is false', () => {
    renderManager([makeIolImage()], false);
    expect(mockApiRequest).not.toHaveBeenCalled();
  });

  // ── autoRefreshIolBuilds toggle ──

  it('can toggle auto-refresh', () => {
    const { result } = renderManager();

    expect(result.current.autoRefreshIolBuilds).toBe(true);

    act(() => {
      result.current.setAutoRefreshIolBuilds(false);
    });

    expect(result.current.autoRefreshIolBuilds).toBe(false);
  });
});
