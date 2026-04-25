import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useStudioModals } from './useStudioModals';
import type { TaskLogEntry } from '../components/TaskLogPanel';

const entry = (overrides: Partial<TaskLogEntry> = {}): TaskLogEntry => ({
  id: 'e1',
  timestamp: new Date(0),
  level: 'info',
  message: 'hello',
  ...overrides,
});

describe('useStudioModals', () => {
  describe('config viewer', () => {
    it('starts closed with no node or snapshot', () => {
      const { result } = renderHook(() => useStudioModals());
      expect(result.current.configViewerOpen).toBe(false);
      expect(result.current.configViewerNode).toBeNull();
      expect(result.current.configViewerSnapshot).toBeNull();
    });

    it('opens with node + snapshot when both are provided', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.handleOpenConfigViewer('n1', 'router-a', 'hostname r1', 'snapshot-1');
      });

      expect(result.current.configViewerOpen).toBe(true);
      expect(result.current.configViewerNode).toEqual({ id: 'n1', name: 'router-a' });
      expect(result.current.configViewerSnapshot).toEqual({
        content: 'hostname r1',
        label: 'snapshot-1',
      });
    });

    it('opens with null node when nodeId or nodeName is missing', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.handleOpenConfigViewer(undefined, undefined, 'cfg', 'label');
      });

      expect(result.current.configViewerOpen).toBe(true);
      expect(result.current.configViewerNode).toBeNull();
      expect(result.current.configViewerSnapshot).toEqual({ content: 'cfg', label: 'label' });
    });

    it('opens with null snapshot when content is missing or label is empty', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.handleOpenConfigViewer('n1', 'r1');
      });
      expect(result.current.configViewerSnapshot).toBeNull();

      act(() => {
        result.current.handleCloseConfigViewer();
        result.current.handleOpenConfigViewer('n1', 'r1', 'cfg', undefined);
      });
      expect(result.current.configViewerSnapshot).toBeNull();
    });

    it('preserves snapshot content of empty string when label is provided', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.handleOpenConfigViewer('n1', 'r1', '', 'label');
      });

      expect(result.current.configViewerSnapshot).toEqual({ content: '', label: 'label' });
    });

    it('closes via handleCloseConfigViewer', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.handleOpenConfigViewer('n1', 'r1', 'c', 'l');
      });
      expect(result.current.configViewerOpen).toBe(true);

      act(() => {
        result.current.handleCloseConfigViewer();
      });
      expect(result.current.configViewerOpen).toBe(false);
    });
  });

  describe('handleTaskLogEntryClick', () => {
    it('opens the job log modal when entry has jobId', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.handleTaskLogEntryClick(entry({ jobId: 'job-42' }));
      });

      expect(result.current.jobLogModalOpen).toBe(true);
      expect(result.current.jobLogModalJobId).toBe('job-42');
      expect(result.current.taskLogEntryModalOpen).toBe(false);
    });

    it('opens the task log entry modal when entry lacks jobId', () => {
      const { result } = renderHook(() => useStudioModals());
      const e = entry({ id: 'plain', message: 'no job' });

      act(() => {
        result.current.handleTaskLogEntryClick(e);
      });

      expect(result.current.taskLogEntryModalOpen).toBe(true);
      expect(result.current.taskLogEntryModalEntry).toEqual(e);
      expect(result.current.jobLogModalOpen).toBe(false);
    });

    it('closes the job log and task log modals via their handlers', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.handleTaskLogEntryClick(entry({ jobId: 'j1' }));
      });
      expect(result.current.jobLogModalOpen).toBe(true);
      act(() => {
        result.current.handleCloseJobLogModal();
      });
      expect(result.current.jobLogModalOpen).toBe(false);

      act(() => {
        result.current.handleTaskLogEntryClick(entry());
      });
      expect(result.current.taskLogEntryModalOpen).toBe(true);
      act(() => {
        result.current.handleCloseTaskLogEntryModal();
      });
      expect(result.current.taskLogEntryModalOpen).toBe(false);
    });
  });

  describe('YAML preview', () => {
    it('starts closed with empty content', () => {
      const { result } = renderHook(() => useStudioModals());
      expect(result.current.showYamlModal).toBe(false);
      expect(result.current.yamlContent).toBe('');
    });

    it('opens with provided YAML content', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.openYamlPreview('foo: bar\n');
      });

      expect(result.current.showYamlModal).toBe(true);
      expect(result.current.yamlContent).toBe('foo: bar\n');
    });

    it('closes via closeYamlPreview', () => {
      const { result } = renderHook(() => useStudioModals());

      act(() => {
        result.current.openYamlPreview('x: 1');
      });
      act(() => {
        result.current.closeYamlPreview();
      });

      expect(result.current.showYamlModal).toBe(false);
    });
  });
});
