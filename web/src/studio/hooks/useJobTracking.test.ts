/**
 * Tests for useJobTracking hook.
 *
 * These tests verify:
 * 1. Job loading from API
 * 2. Job status transition tracking and notifications
 * 3. Task log entry creation and filtering
 * 4. WebSocket job progress handling
 * 5. WebSocket test result handling
 * 6. Scenario step handling
 * 7. Reset behavior
 * 8. Initial load suppression (no re-logging existing jobs)
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useJobTracking } from './useJobTracking';
import type { NotificationLevel } from '../../types/notifications';

// Mock usePersistedState to use plain useState (avoids localStorage in tests)
vi.mock('./usePersistedState', async () => {
  const react = await import('react');
  return {
    usePersistedState: <T,>(_key: string, defaultValue: T): [T, (v: T | ((p: T) => T)) => void] => {
      return react.useState<T>(defaultValue);
    },
  };
});

function createMockOptions() {
  const notifications: Array<{ level: NotificationLevel; title: string; message?: string; options?: Record<string, unknown> }> = [];
  return {
    studioRequest: vi.fn(),
    addNotification: vi.fn((level: NotificationLevel, title: string, message?: string, options?: Record<string, unknown>) => {
      notifications.push({ level, title, message, options });
    }),
    notifications,
  };
}

describe('useJobTracking', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ============================================================================
  // Job Loading
  // ============================================================================

  describe('loadJobs', () => {
    it('loads jobs from the API and stores them', async () => {
      const { studioRequest, addNotification } = createMockOptions();
      const mockJobs = [
        { id: 'job-1', action: 'deploy', status: 'completed', lab_id: 'lab-1' },
        { id: 'job-2', action: 'destroy', status: 'running', lab_id: 'lab-1' },
      ];
      studioRequest.mockResolvedValue({ jobs: mockJobs });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(studioRequest).toHaveBeenCalledWith('/labs/lab-1/jobs');
      expect(result.current.jobs).toHaveLength(2);
      expect(result.current.jobs[0].id).toBe('job-1');
    });

    it('handles empty job list from API', async () => {
      const { studioRequest, addNotification } = createMockOptions();
      studioRequest.mockResolvedValue({ jobs: [] });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(result.current.jobs).toHaveLength(0);
    });

    it('handles missing jobs field in API response', async () => {
      const { studioRequest, addNotification } = createMockOptions();
      studioRequest.mockResolvedValue({});

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(result.current.jobs).toHaveLength(0);
    });
  });

  // ============================================================================
  // Initial Load Suppression
  // ============================================================================

  describe('initial load suppression', () => {
    it('does not log or notify for jobs on initial load', async () => {
      const { studioRequest, addNotification } = createMockOptions();
      const mockJobs = [
        { id: 'job-1', action: 'deploy', status: 'completed', lab_id: 'lab-1' },
        { id: 'job-2', action: 'destroy', status: 'failed', lab_id: 'lab-1', error_summary: 'timeout' },
      ];
      studioRequest.mockResolvedValue({ jobs: mockJobs });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // On initial load, jobs are recorded but no notifications or task log entries
      expect(addNotification).not.toHaveBeenCalled();
      expect(result.current.filteredTaskLog).toHaveLength(0);
    });
  });

  // ============================================================================
  // Job Status Transitions
  // ============================================================================

  describe('job status transitions', () => {
    it('notifies when a job transitions from queued to running', async () => {
      const { studioRequest, addNotification } = createMockOptions();

      // First load: job is queued
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-1', action: 'deploy', status: 'queued', lab_id: 'lab-1' }],
      });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // Initial load -- no notifications
      expect(addNotification).not.toHaveBeenCalled();

      // Second load: job is now running
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-1', action: 'deploy', status: 'running', lab_id: 'lab-1' }],
      });

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(addNotification).toHaveBeenCalledWith(
        'info',
        'DEPLOY started',
        undefined,
        expect.objectContaining({ jobId: 'job-1', category: 'job-start' })
      );
    });

    it('notifies when a job transitions from running to completed', async () => {
      const { studioRequest, addNotification } = createMockOptions();

      // First load
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-1', action: 'deploy', status: 'queued', lab_id: 'lab-1' }],
      });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // Transition to completed
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-1', action: 'deploy', status: 'completed', lab_id: 'lab-1' }],
      });

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(addNotification).toHaveBeenCalledWith(
        'success',
        'DEPLOY completed',
        undefined,
        expect.objectContaining({ jobId: 'job-1', category: 'job-complete' })
      );
    });

    it('notifies with error details when a job transitions to failed', async () => {
      const { studioRequest, addNotification } = createMockOptions();

      // First load
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-1', action: 'deploy', status: 'running', lab_id: 'lab-1' }],
      });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // Transition to failed
      studioRequest.mockResolvedValueOnce({
        jobs: [{
          id: 'job-1',
          action: 'deploy',
          status: 'failed',
          lab_id: 'lab-1',
          error_summary: 'Container creation failed',
        }],
      });

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(addNotification).toHaveBeenCalledWith(
        'error',
        'DEPLOY failed',
        'Container creation failed',
        expect.objectContaining({ jobId: 'job-1', category: 'job-failed' })
      );
    });

    it('uses sync- category prefix for sync jobs', async () => {
      const { studioRequest, addNotification } = createMockOptions();

      // First load with sync job queued
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-s1', action: 'sync:start', status: 'queued', lab_id: 'lab-1' }],
      });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // Transition to completed
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-s1', action: 'sync:start', status: 'completed', lab_id: 'lab-1' }],
      });

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(addNotification).toHaveBeenCalledWith(
        'success',
        expect.any(String),
        undefined,
        expect.objectContaining({ category: 'sync-job-complete' })
      );
    });

    it('formats node action labels correctly', async () => {
      const { studioRequest, addNotification } = createMockOptions();

      // First load with node action queued
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-n1', action: 'node:start:R1', status: 'queued', lab_id: 'lab-1' }],
      });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // Transition to running
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-n1', action: 'node:start:R1', status: 'running', lab_id: 'lab-1' }],
      });

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(addNotification).toHaveBeenCalledWith(
        'info',
        'Node start (R1) started',
        undefined,
        expect.any(Object)
      );
    });
  });

  // ============================================================================
  // Task Log
  // ============================================================================

  describe('task log', () => {
    it('adds entries via addTaskLogEntry', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.addTaskLogEntry('info', 'Test message', 'job-1');
      });

      expect(result.current.taskLog).toHaveLength(1);
      expect(result.current.taskLog[0].level).toBe('info');
      expect(result.current.taskLog[0].message).toBe('Test message');
      expect(result.current.taskLog[0].jobId).toBe('job-1');
    });

    it('limits task log to 100 entries', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        for (let i = 0; i < 105; i++) {
          result.current.addTaskLogEntry('info', `Message ${i}`);
        }
      });

      // The hook keeps the last 100 (slices -99 before adding new one)
      expect(result.current.taskLog.length).toBeLessThanOrEqual(100);
    });

    it('clears task log entries by setting cleared timestamp', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.addTaskLogEntry('info', 'Before clear');
      });

      expect(result.current.filteredTaskLog).toHaveLength(1);

      act(() => {
        result.current.clearTaskLog();
      });

      // filteredTaskLog should be empty after clear
      expect(result.current.filteredTaskLog).toHaveLength(0);
      // But raw taskLog still has the entry
      expect(result.current.taskLog).toHaveLength(1);
    });

    it('shows entries added after clear in filteredTaskLog', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.addTaskLogEntry('info', 'Before clear');
      });

      act(() => {
        result.current.clearTaskLog();
      });

      // Advance time so new entry is after clear timestamp
      vi.advanceTimersByTime(10);

      act(() => {
        result.current.addTaskLogEntry('success', 'After clear');
      });

      expect(result.current.filteredTaskLog).toHaveLength(1);
      expect(result.current.filteredTaskLog[0].message).toBe('After clear');
    });
  });

  // ============================================================================
  // WebSocket Job Progress
  // ============================================================================

  describe('handleWSJobProgress', () => {
    it('shows error notification for failed jobs', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.handleWSJobProgress({
          job_id: 'job-1',
          action: 'deploy',
          status: 'failed',
          error_message: 'Network timeout',
        });
      });

      expect(addNotification).toHaveBeenCalledWith(
        'error',
        'Job Failed: deploy',
        'Network timeout',
        expect.objectContaining({ jobId: 'job-1', category: 'job-failed' })
      );
    });

    it('shows success notification for completed jobs', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.handleWSJobProgress({
          job_id: 'job-2',
          action: 'deploy',
          status: 'completed',
          progress_message: 'All nodes deployed',
        });
      });

      expect(addNotification).toHaveBeenCalledWith(
        'success',
        'Job Completed: deploy',
        'All nodes deployed',
        expect.objectContaining({ jobId: 'job-2', category: 'job-complete' })
      );
    });

    it('uses default message when completed without progress_message', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.handleWSJobProgress({
          job_id: 'job-3',
          action: 'destroy',
          status: 'completed',
        });
      });

      expect(addNotification).toHaveBeenCalledWith(
        'success',
        'Job Completed: destroy',
        'Operation completed successfully',
        expect.any(Object)
      );
    });

    it('uses sync- category prefix for sync actions via WS', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.handleWSJobProgress({
          job_id: 'job-s1',
          action: 'sync:stop',
          status: 'failed',
          error_message: 'Agent unreachable',
        });
      });

      expect(addNotification).toHaveBeenCalledWith(
        'error',
        'Job Failed: sync:stop',
        'Agent unreachable',
        expect.objectContaining({ category: 'sync-job-failed' })
      );
    });

    it('does not notify for queued or running status via WS progress', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.handleWSJobProgress({
          job_id: 'job-4',
          action: 'deploy',
          status: 'running',
          progress_message: 'Deploying...',
        });
      });

      expect(addNotification).not.toHaveBeenCalled();
    });
  });

  // ============================================================================
  // WebSocket Test Results
  // ============================================================================

  describe('handleWSTestResult', () => {
    it('accumulates test results', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.setTestRunning(true);
        result.current.handleWSTestResult({
          job_id: 'test-job-1',
          result: {
            spec_index: 0,
            spec_name: 'ping R1 to R2',
            status: 'passed',
            duration_ms: 120,
          },
          summary: { total: 2, passed: 1, failed: 0, errors: 0 },
        });
      });

      expect(result.current.testResults).toHaveLength(1);
      expect(result.current.testSummary).toEqual({ total: 2, passed: 1, failed: 0, errors: 0 });
      expect(result.current.testRunning).toBe(true);
    });

    it('marks test run complete when all results received', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.setTestRunning(true);
      });

      act(() => {
        result.current.handleWSTestResult({
          job_id: 'test-job-1',
          result: {
            spec_index: 0,
            spec_name: 'ping R1 to R2',
            status: 'passed',
            duration_ms: 120,
          },
          summary: { total: 2, passed: 1, failed: 0, errors: 0 },
        });
      });

      expect(result.current.testRunning).toBe(true);

      act(() => {
        result.current.handleWSTestResult({
          job_id: 'test-job-1',
          result: {
            spec_index: 1,
            spec_name: 'ping R2 to R3',
            status: 'failed',
            duration_ms: 5000,
          },
          summary: { total: 2, passed: 1, failed: 1, errors: 0 },
        });
      });

      // passed + failed + errors >= total => testRunning = false
      expect(result.current.testRunning).toBe(false);
    });
  });

  // ============================================================================
  // Scenario Steps
  // ============================================================================

  describe('handleWSScenarioStep', () => {
    it('adds scenario steps', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.setActiveScenarioJobId('scenario-job-1');
        result.current.handleWSScenarioStep({
          job_id: 'scenario-job-1',
          step_index: 0,
          step_name: 'Configure interfaces',
          step_type: 'command',
          status: 'running',
          total_steps: 3,
        });
      });

      expect(result.current.scenarioSteps).toHaveLength(1);
      expect(result.current.scenarioSteps[0].step_name).toBe('Configure interfaces');
    });

    it('updates existing step by index', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.handleWSScenarioStep({
          job_id: 'scenario-job-1',
          step_index: 0,
          step_name: 'Configure interfaces',
          step_type: 'command',
          status: 'running',
          total_steps: 3,
        });
      });

      act(() => {
        result.current.handleWSScenarioStep({
          job_id: 'scenario-job-1',
          step_index: 0,
          step_name: 'Configure interfaces',
          step_type: 'command',
          status: 'passed',
          total_steps: 3,
          duration_ms: 500,
        });
      });

      expect(result.current.scenarioSteps).toHaveLength(1);
      expect(result.current.scenarioSteps[0].status).toBe('passed');
    });

    it('clears active scenario job on completion signal (step_index -1)', () => {
      const { studioRequest, addNotification } = createMockOptions();

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      act(() => {
        result.current.setActiveScenarioJobId('scenario-job-1');
      });

      expect(result.current.activeScenarioJobId).toBe('scenario-job-1');

      act(() => {
        result.current.handleWSScenarioStep({
          job_id: 'scenario-job-1',
          step_index: -1,
          step_name: '',
          step_type: '',
          status: 'passed',
          total_steps: 0,
        });
      });

      expect(result.current.activeScenarioJobId).toBeNull();
    });
  });

  // ============================================================================
  // Reset
  // ============================================================================

  describe('resetJobTracking', () => {
    it('clears jobs and resets initial load flag', async () => {
      const { studioRequest, addNotification } = createMockOptions();

      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-1', action: 'deploy', status: 'completed', lab_id: 'lab-1' }],
      });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      expect(result.current.jobs).toHaveLength(1);

      act(() => {
        result.current.resetJobTracking();
      });

      expect(result.current.jobs).toHaveLength(0);

      // After reset, loading new jobs should not trigger notifications (initial load suppression again)
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-2', action: 'deploy', status: 'failed', lab_id: 'lab-2', error_summary: 'err' }],
      });

      await act(async () => {
        await result.current.loadJobs('lab-2', []);
      });

      // The initial load after reset should NOT fire notifications
      expect(addNotification).not.toHaveBeenCalled();
    });
  });

  // ============================================================================
  // Multiple Concurrent Jobs
  // ============================================================================

  describe('multiple concurrent jobs', () => {
    it('tracks status changes across multiple jobs independently', async () => {
      const { studioRequest, addNotification } = createMockOptions();

      // Initial load: two jobs, both queued
      studioRequest.mockResolvedValueOnce({
        jobs: [
          { id: 'job-a', action: 'deploy', status: 'queued', lab_id: 'lab-1' },
          { id: 'job-b', action: 'destroy', status: 'queued', lab_id: 'lab-1' },
        ],
      });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // Second load: job-a transitions to completed, job-b to running
      studioRequest.mockResolvedValueOnce({
        jobs: [
          { id: 'job-a', action: 'deploy', status: 'completed', lab_id: 'lab-1' },
          { id: 'job-b', action: 'destroy', status: 'running', lab_id: 'lab-1' },
        ],
      });

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // Both transitions should have generated notifications
      expect(addNotification).toHaveBeenCalledTimes(2);
      expect(addNotification).toHaveBeenCalledWith(
        'success',
        'DEPLOY completed',
        undefined,
        expect.any(Object)
      );
      expect(addNotification).toHaveBeenCalledWith(
        'info',
        'DESTROY started',
        undefined,
        expect.any(Object)
      );
    });

    it('handles new jobs appearing in subsequent loads', async () => {
      const { studioRequest, addNotification } = createMockOptions();

      // Initial load
      studioRequest.mockResolvedValueOnce({
        jobs: [{ id: 'job-a', action: 'deploy', status: 'completed', lab_id: 'lab-1' }],
      });

      const { result } = renderHook(() =>
        useJobTracking({ studioRequest, addNotification })
      );

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // New job appears
      studioRequest.mockResolvedValueOnce({
        jobs: [
          { id: 'job-a', action: 'deploy', status: 'completed', lab_id: 'lab-1' },
          { id: 'job-b', action: 'destroy', status: 'running', lab_id: 'lab-1' },
        ],
      });

      await act(async () => {
        await result.current.loadJobs('lab-1', []);
      });

      // New job-b running should trigger notification
      expect(addNotification).toHaveBeenCalledWith(
        'info',
        'DESTROY started',
        undefined,
        expect.any(Object)
      );
    });
  });
});
