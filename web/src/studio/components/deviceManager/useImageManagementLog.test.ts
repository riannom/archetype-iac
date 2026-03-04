/**
 * Tests for useImageManagementLog hook.
 *
 * These tests verify:
 * 1. addImageManagementLog — adds entries with auto-generated id and timestamp
 * 2. copyUploadLogEntry — clipboard copy with fallback
 * 3. filteredImageManagementLogs — filter by type/status and search
 * 4. imageLogCounts — correct count computation per category
 * 5. uploadErrorCount — error count
 * 6. Log clearing
 * 7. Log limit enforcement
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useImageManagementLog } from './useImageManagementLog';
import type { ImageManagementLogEntry, ImageManagementLogFilter } from './deviceManagerTypes';

// Mock usePersistedState to use plain useState (avoids localStorage side effects)
vi.mock('../../hooks/usePersistedState', async () => {
  const react = await import('react');
  return {
    usePersistedState: <T,>(_key: string, defaultValue: T): [T, (v: T | ((p: T) => T)) => void] => {
      return react.useState<T>(defaultValue);
    },
  };
});

// ============================================================================
// Helpers
// ============================================================================

function makeLogEntry(overrides: Partial<ImageManagementLogEntry> = {}): Omit<ImageManagementLogEntry, 'id' | 'timestamp'> {
  return {
    level: 'info',
    category: 'docker',
    phase: 'upload',
    message: 'Image uploaded successfully',
    ...overrides,
  };
}

// ============================================================================
// Tests
// ============================================================================

describe('useImageManagementLog', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Initial State ──

  it('starts with empty logs and default filter', () => {
    const { result } = renderHook(() => useImageManagementLog());

    expect(result.current.imageManagementLogs).toEqual([]);
    expect(result.current.imageLogFilter).toBe('all');
    expect(result.current.imageLogSearch).toBe('');
    expect(result.current.showUploadLogsModal).toBe(false);
    expect(result.current.copiedUploadLogId).toBeNull();
    expect(result.current.uploadErrorCount).toBe(0);
  });

  // ── addImageManagementLog ──

  describe('addImageManagementLog', () => {
    it('adds entry with auto-generated id and timestamp', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry());
      });

      expect(result.current.imageManagementLogs).toHaveLength(1);
      const entry = result.current.imageManagementLogs[0];
      expect(entry.id).toMatch(/^img-log-/);
      expect(entry.timestamp).toBeTruthy();
      expect(entry.level).toBe('info');
      expect(entry.category).toBe('docker');
      expect(entry.phase).toBe('upload');
      expect(entry.message).toBe('Image uploaded successfully');
    });

    it('prepends new entries (newest first)', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ message: 'First' }));
      });
      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ message: 'Second' }));
      });

      expect(result.current.imageManagementLogs[0].message).toBe('Second');
      expect(result.current.imageManagementLogs[1].message).toBe('First');
    });

    it('includes optional filename and details', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({
          filename: 'ceos.tar',
          details: 'SHA256 mismatch detected',
        }));
      });

      expect(result.current.imageManagementLogs[0].filename).toBe('ceos.tar');
      expect(result.current.imageManagementLogs[0].details).toBe('SHA256 mismatch detected');
    });

    it('enforces log limit (IMAGE_LOG_LIMIT = 200)', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        for (let i = 0; i < 210; i++) {
          result.current.addImageManagementLog(makeLogEntry({ message: `Entry ${i}` }));
        }
      });

      expect(result.current.imageManagementLogs.length).toBeLessThanOrEqual(200);
    });
  });

  // ── clearImageManagementLogs ──

  describe('clearImageManagementLogs', () => {
    it('clears all log entries', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ message: 'Entry 1' }));
        result.current.addImageManagementLog(makeLogEntry({ message: 'Entry 2' }));
      });

      expect(result.current.imageManagementLogs.length).toBeGreaterThan(0);

      act(() => {
        result.current.clearImageManagementLogs();
      });

      expect(result.current.imageManagementLogs).toEqual([]);
    });
  });

  // ── imageLogCounts ──

  describe('imageLogCounts', () => {
    it('computes counts by category', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker', level: 'info' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker', level: 'error' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'iso', level: 'info' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'qcow2', level: 'info' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'qcow2', level: 'error' }));
      });

      expect(result.current.imageLogCounts).toEqual({
        all: 5,
        errors: 2,
        iso: 1,
        docker: 2,
        qcow2: 2,
      });
    });

    it('returns zeroes when log is empty', () => {
      const { result } = renderHook(() => useImageManagementLog());

      expect(result.current.imageLogCounts).toEqual({
        all: 0,
        errors: 0,
        iso: 0,
        docker: 0,
        qcow2: 0,
      });
    });
  });

  // ── uploadErrorCount ──

  describe('uploadErrorCount', () => {
    it('counts only error-level entries', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ level: 'info' }));
        result.current.addImageManagementLog(makeLogEntry({ level: 'error' }));
        result.current.addImageManagementLog(makeLogEntry({ level: 'error' }));
        result.current.addImageManagementLog(makeLogEntry({ level: 'info' }));
      });

      expect(result.current.uploadErrorCount).toBe(2);
    });

    it('returns 0 when no errors exist', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ level: 'info' }));
      });

      expect(result.current.uploadErrorCount).toBe(0);
    });
  });

  // ── filteredImageManagementLogs ──

  describe('filteredImageManagementLogs', () => {
    it('returns all logs when filter is "all" and no search', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'iso' }));
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(2);
    });

    it('filters by "errors" showing only error-level entries', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ level: 'info', message: 'ok' }));
        result.current.addImageManagementLog(makeLogEntry({ level: 'error', message: 'fail' }));
      });

      act(() => {
        result.current.setImageLogFilter('errors');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
      expect(result.current.filteredImageManagementLogs[0].level).toBe('error');
    });

    it('filters by category (iso)', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'iso' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'qcow2' }));
      });

      act(() => {
        result.current.setImageLogFilter('iso');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
      expect(result.current.filteredImageManagementLogs[0].category).toBe('iso');
    });

    it('filters by category (docker)', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'iso' }));
      });

      act(() => {
        result.current.setImageLogFilter('docker');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
      expect(result.current.filteredImageManagementLogs[0].category).toBe('docker');
    });

    it('filters by category (qcow2)', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ category: 'qcow2' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker' }));
      });

      act(() => {
        result.current.setImageLogFilter('qcow2');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
      expect(result.current.filteredImageManagementLogs[0].category).toBe('qcow2');
    });

    it('applies search query within filtered results', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ message: 'Upload started for ceos.tar' }));
        result.current.addImageManagementLog(makeLogEntry({ message: 'Upload complete for srlinux.tar' }));
      });

      act(() => {
        result.current.setImageLogSearch('ceos');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
      expect(result.current.filteredImageManagementLogs[0].message).toContain('ceos');
    });

    it('search is case-insensitive', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ message: 'Upload FAILED for CEOS' }));
      });

      act(() => {
        result.current.setImageLogSearch('ceos');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
    });

    it('search matches against filename', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({
          message: 'Upload done',
          filename: 'special-image.tar',
        }));
        result.current.addImageManagementLog(makeLogEntry({ message: 'Other entry' }));
      });

      act(() => {
        result.current.setImageLogSearch('special-image');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
    });

    it('search matches against details', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({
          message: 'Error occurred',
          details: 'Connection refused at 10.0.0.1:8001',
        }));
        result.current.addImageManagementLog(makeLogEntry({ message: 'Other entry' }));
      });

      act(() => {
        result.current.setImageLogSearch('Connection refused');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
    });

    it('search matches against category and phase', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ category: 'iso', phase: 'extraction' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker', phase: 'upload' }));
      });

      act(() => {
        result.current.setImageLogSearch('extraction');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
    });

    it('returns empty when no entries match search', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ message: 'Hello world' }));
      });

      act(() => {
        result.current.setImageLogSearch('nonexistent');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(0);
    });

    it('combines filter and search', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker', level: 'error', message: 'Docker fail' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'docker', level: 'info', message: 'Docker ok' }));
        result.current.addImageManagementLog(makeLogEntry({ category: 'iso', level: 'error', message: 'ISO fail' }));
      });

      act(() => {
        result.current.setImageLogFilter('errors');
      });
      act(() => {
        result.current.setImageLogSearch('docker');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(1);
      expect(result.current.filteredImageManagementLogs[0].message).toBe('Docker fail');
    });

    it('ignores whitespace-only search', () => {
      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry());
        result.current.addImageManagementLog(makeLogEntry());
      });

      act(() => {
        result.current.setImageLogSearch('   ');
      });

      expect(result.current.filteredImageManagementLogs).toHaveLength(2);
    });
  });

  // ── copyUploadLogEntry ──

  describe('copyUploadLogEntry', () => {
    it('copies entry text to clipboard and sets copiedUploadLogId', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: { writeText },
      });

      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({
          message: 'Test message',
          filename: 'test.tar',
        }));
      });

      const entry = result.current.imageManagementLogs[0];

      await act(async () => {
        await result.current.copyUploadLogEntry(entry);
      });

      expect(writeText).toHaveBeenCalledTimes(1);
      const copiedText = writeText.mock.calls[0][0] as string;
      expect(copiedText).toContain('message: Test message');
      expect(copiedText).toContain('filename: test.tar');
      expect(copiedText).toContain('level: info');
      expect(copiedText).toContain('category: docker');
      expect(copiedText).toContain('phase: upload');
      expect(result.current.copiedUploadLogId).toBe(entry.id);
    });

    it('falls back to execCommand when clipboard API is unavailable', async () => {
      // Remove clipboard API
      Object.assign(navigator, { clipboard: undefined });

      const execCommand = vi.fn().mockReturnValue(true);
      document.execCommand = execCommand;

      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({ message: 'Fallback test' }));
      });

      const entry = result.current.imageManagementLogs[0];

      await act(async () => {
        await result.current.copyUploadLogEntry(entry);
      });

      expect(execCommand).toHaveBeenCalledWith('copy');
      expect(result.current.copiedUploadLogId).toBe(entry.id);
    });

    it('clears copiedUploadLogId after 1500ms', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: { writeText },
      });

      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry());
      });

      const entry = result.current.imageManagementLogs[0];

      await act(async () => {
        await result.current.copyUploadLogEntry(entry);
      });

      expect(result.current.copiedUploadLogId).toBe(entry.id);

      act(() => {
        vi.advanceTimersByTime(1500);
      });

      expect(result.current.copiedUploadLogId).toBeNull();
    });

    it('sets copiedUploadLogId to null on copy failure', async () => {
      Object.assign(navigator, {
        clipboard: {
          writeText: vi.fn().mockRejectedValue(new Error('Permission denied')),
        },
      });

      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry());
      });

      const entry = result.current.imageManagementLogs[0];

      await act(async () => {
        await result.current.copyUploadLogEntry(entry);
      });

      expect(result.current.copiedUploadLogId).toBeNull();
      consoleSpy.mockRestore();
    });

    it('includes details in copied text when present', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: { writeText },
      });

      const { result } = renderHook(() => useImageManagementLog());

      act(() => {
        result.current.addImageManagementLog(makeLogEntry({
          message: 'Error occurred',
          details: 'Stack trace line 1\nStack trace line 2',
        }));
      });

      const entry = result.current.imageManagementLogs[0];

      await act(async () => {
        await result.current.copyUploadLogEntry(entry);
      });

      const copiedText = writeText.mock.calls[0][0] as string;
      expect(copiedText).toContain('details:');
      expect(copiedText).toContain('Stack trace line 1');
    });
  });

  // ── Modal and UI State ──

  describe('UI state', () => {
    it('toggles showUploadLogsModal', () => {
      const { result } = renderHook(() => useImageManagementLog());

      expect(result.current.showUploadLogsModal).toBe(false);

      act(() => {
        result.current.setShowUploadLogsModal(true);
      });

      expect(result.current.showUploadLogsModal).toBe(true);
    });
  });
});
