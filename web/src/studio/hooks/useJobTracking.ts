import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { TaskLogEntry } from '../components/TaskLogPanel';
import { JobProgressData, ScenarioStepData } from './useLabStateWS';
import { usePersistedState } from './usePersistedState';
import type { Notification, NotificationLevel } from '../../types/notifications';

interface UseJobTrackingOptions {
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  addNotification: (level: NotificationLevel, title: string, message?: string, options?: Partial<Notification>) => void;
}

export function useJobTracking({
  studioRequest,
  addNotification,
}: UseJobTrackingOptions) {
  const [jobs, setJobs] = useState<any[]>([]);
  const prevJobsRef = useRef<Map<string, string>>(new Map());
  const isInitialJobLoadRef = useRef(true);
  const [taskLog, setTaskLog] = useState<TaskLogEntry[]>([]);
  const [isTaskLogVisible, setIsTaskLogVisible] = usePersistedState('archetype-tasklog-visible', true);
  const [taskLogAutoRefresh, setTaskLogAutoRefresh] = usePersistedState('archetype-tasklog-auto-refresh', true);
  const [taskLogClearedAt, setTaskLogClearedAt] = useState<number>(() => {
    const stored = localStorage.getItem('archetype_tasklog_cleared_at');
    return stored ? parseInt(stored, 10) : 0;
  });

  // Test state
  const [testResults, setTestResults] = useState<import('../types').TestResult[]>([]);
  const [testSummary, setTestSummary] = useState<{ total: number; passed: number; failed: number; errors: number } | null>(null);
  const [testRunning, setTestRunning] = useState(false);

  // Scenario engine state
  const [scenarioSteps, setScenarioSteps] = useState<ScenarioStepData[]>([]);
  const [activeScenarioJobId, setActiveScenarioJobId] = useState<string | null>(null);

  const addTaskLogEntry = useCallback((level: TaskLogEntry['level'], message: string, jobId?: string) => {
    const id = `log-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setTaskLog((prev) => [...prev.slice(-99), { id, timestamp: new Date(), level, message, jobId }]);
  }, []);

  const clearTaskLog = useCallback(() => {
    const now = Date.now();
    setTaskLogClearedAt(now);
    localStorage.setItem('archetype_tasklog_cleared_at', now.toString());
  }, []);

  const filteredTaskLog = useMemo(
    () => taskLog.filter((entry) => entry.timestamp.getTime() > taskLogClearedAt),
    [taskLog, taskLogClearedAt]
  );

  const loadJobs = useCallback(async (labId: string, _currentNodes: unknown[]) => {
    // Also load jobs for job log display
    const data = await studioRequest<{ jobs: any[] }>(`/labs/${labId}/jobs`);
    setJobs(data.jobs || []);
  }, [studioRequest]);

  // Handle WebSocket job progress - show toast for job failures/completions
  const handleWSJobProgress = useCallback((job: JobProgressData) => {
    const prefix = job.action.startsWith('sync:') ? 'sync-' : '';
    if (job.status === 'failed' && job.error_message) {
      addNotification(
        'error',
        `Job Failed: ${job.action}`,
        job.error_message,
        { jobId: job.job_id, category: `${prefix}job-failed` }
      );
    } else if (job.status === 'completed') {
      addNotification(
        'success',
        `Job Completed: ${job.action}`,
        job.progress_message || 'Operation completed successfully',
        { jobId: job.job_id, category: `${prefix}job-complete` }
      );
    }
  }, [addNotification]);

  // Handle WebSocket test results
  const handleWSTestResult = useCallback((data: import('./useLabStateWS').TestResultData) => {
    setTestResults(prev => [...prev, data.result]);
    setTestSummary(data.summary);
    if (data.summary.passed + data.summary.failed + data.summary.errors >= data.summary.total) {
      setTestRunning(false);
    }
  }, []);

  const handleWSScenarioStep = useCallback((data: ScenarioStepData) => {
    if (data.step_index === -1) {
      // Completion signal
      setActiveScenarioJobId(null);
      return;
    }
    setScenarioSteps(prev => {
      const existing = prev.findIndex(s => s.step_index === data.step_index);
      if (existing >= 0) {
        const next = [...prev];
        next[existing] = data;
        return next;
      }
      return [...prev, data];
    });
  }, []);

  // Track job status changes, log them, and trigger notifications
  useEffect(() => {
    const prevStatuses = prevJobsRef.current;
    const newStatuses = new Map<string, string>();

    // On initial load, just populate the ref without logging
    // This prevents re-logging all existing jobs on page refresh
    if (isInitialJobLoadRef.current && jobs.length > 0) {
      for (const job of jobs) {
        newStatuses.set(job.id, job.status);
      }
      prevJobsRef.current = newStatuses;
      isInitialJobLoadRef.current = false;
      return;
    }

    // Determine notification category prefix based on job action
    const categoryPrefix = (action: string) => action.startsWith('sync:') ? 'sync-' : '';
    const maybeLogJobEvent = (
      level: TaskLogEntry['level'],
      message: string,
      jobId: string
    ) => {
      if (taskLogAutoRefresh) {
        addTaskLogEntry(level, message, jobId);
      }
    };

    for (const job of jobs) {
      const jobKey = job.id;
      const prevStatus = prevStatuses.get(jobKey);
      newStatuses.set(jobKey, job.status);
      const prefix = categoryPrefix(job.action);

      if (prevStatus && prevStatus !== job.status) {
        const actionLabel = job.action.startsWith('node:')
          ? `Node ${job.action.split(':')[1]} (${job.action.split(':')[2]})`
          : job.action.toUpperCase();

        if (job.status === 'running') {
          maybeLogJobEvent('info', `Job running: ${actionLabel}`, job.id);
          addNotification('info', `${actionLabel} started`, undefined, {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-start`,
          });
        } else if (job.status === 'completed') {
          maybeLogJobEvent('success', `Job completed: ${actionLabel}`, job.id);
          addNotification('success', `${actionLabel} completed`, undefined, {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-complete`,
          });
        } else if (job.status === 'failed') {
          const errorDetail = job.error_summary ? `: ${job.error_summary}` : '';
          maybeLogJobEvent('error', `Job failed: ${actionLabel}${errorDetail}`, job.id);
          addNotification('error', `${actionLabel} failed`, job.error_summary || 'Check logs for details', {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-failed`,
          });
        }
      } else if (!prevStatus) {
        // New job - log based on its initial status
        const actionLabel = job.action.startsWith('node:')
          ? `Node ${job.action.split(':')[1]} (${job.action.split(':')[2]})`
          : job.action.toUpperCase();

        if (job.status === 'queued') {
          maybeLogJobEvent('info', `Job queued: ${actionLabel}`, job.id);
        } else if (job.status === 'running') {
          maybeLogJobEvent('info', `Job running: ${actionLabel}`, job.id);
          addNotification('info', `${actionLabel} started`, undefined, {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-start`,
          });
        } else if (job.status === 'completed') {
          maybeLogJobEvent('success', `Job completed: ${actionLabel}`, job.id);
          addNotification('success', `${actionLabel} completed`, undefined, {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-complete`,
          });
        } else if (job.status === 'failed') {
          const errorDetail = job.error_summary ? `: ${job.error_summary}` : '';
          maybeLogJobEvent('error', `Job failed: ${actionLabel}${errorDetail}`, job.id);
          addNotification('error', `${actionLabel} failed`, job.error_summary || 'Check logs for details', {
            jobId: job.id, labId: job.lab_id, category: `${prefix}job-failed`,
          });
        }
      }
    }

    prevJobsRef.current = newStatuses;
  }, [jobs, addTaskLogEntry, addNotification, taskLogAutoRefresh]);

  // Reset job tracking for new lab context
  const resetJobTracking = useCallback(() => {
    setJobs([]);
    prevJobsRef.current = new Map();
    isInitialJobLoadRef.current = true;
  }, []);

  return {
    jobs,
    loadJobs,
    taskLog,
    addTaskLogEntry,
    clearTaskLog,
    filteredTaskLog,
    isTaskLogVisible,
    setIsTaskLogVisible,
    taskLogAutoRefresh,
    setTaskLogAutoRefresh,
    handleWSJobProgress,
    handleWSTestResult,
    handleWSScenarioStep,
    testResults,
    setTestResults,
    testSummary,
    setTestSummary,
    testRunning,
    setTestRunning,
    scenarioSteps,
    setScenarioSteps,
    activeScenarioJobId,
    setActiveScenarioJobId,
    resetJobTracking,
  };
}
