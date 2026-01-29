import React, { useEffect, useState, useCallback } from 'react';
import { apiRequest } from '../api';

interface SyncJob {
  id: string;
  image_id: string;
  host_id: string;
  host_name: string | null;
  status: string;
  progress_percent: number;
  bytes_transferred: number;
  total_bytes: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

interface ImageSyncProgressProps {
  imageId?: string;
  hostId?: string;
  showCompleted?: boolean;
  maxJobs?: number;
  autoRefreshInterval?: number;
  onJobComplete?: (job: SyncJob) => void;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return '-';
  const start = new Date(startedAt).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const seconds = Math.floor((end - start) / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

const statusColors: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  transferring: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  loading: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400',
  completed: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  cancelled: 'bg-stone-100 text-stone-800 dark:bg-stone-900/30 dark:text-stone-400',
};

const statusIcons: Record<string, string> = {
  pending: 'fa-clock',
  transferring: 'fa-arrow-right-arrow-left',
  loading: 'fa-spinner fa-spin',
  completed: 'fa-check',
  failed: 'fa-xmark',
  cancelled: 'fa-ban',
};

export const ImageSyncProgress: React.FC<ImageSyncProgressProps> = ({
  imageId,
  hostId,
  showCompleted = true,
  maxJobs = 10,
  autoRefreshInterval = 2000,
  onJobComplete,
}) => {
  const [jobs, setJobs] = useState<SyncJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchJobs = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (imageId) params.append('image_id', imageId);
      if (hostId) params.append('host_id', hostId);
      params.append('limit', maxJobs.toString());

      const url = `/images/sync-jobs${params.toString() ? `?${params.toString()}` : ''}`;
      const data = await apiRequest<SyncJob[]>(url);

      // Check for newly completed jobs
      if (onJobComplete) {
        data.forEach((newJob) => {
          const oldJob = jobs.find((j) => j.id === newJob.id);
          if (oldJob && oldJob.status !== 'completed' && newJob.status === 'completed') {
            onJobComplete(newJob);
          }
        });
      }

      // Filter if not showing completed
      const filtered = showCompleted
        ? data
        : data.filter((j) => !['completed', 'failed', 'cancelled'].includes(j.status));

      setJobs(filtered);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch sync jobs');
    } finally {
      setLoading(false);
    }
  }, [imageId, hostId, maxJobs, showCompleted, onJobComplete, jobs]);

  useEffect(() => {
    fetchJobs();
  }, [imageId, hostId, maxJobs, showCompleted]);

  // Auto-refresh for active jobs
  useEffect(() => {
    const hasActiveJobs = jobs.some((j) =>
      ['pending', 'transferring', 'loading'].includes(j.status)
    );

    if (!hasActiveJobs) return;

    const interval = setInterval(fetchJobs, autoRefreshInterval);
    return () => clearInterval(interval);
  }, [jobs, autoRefreshInterval, fetchJobs]);

  const handleCancel = async (jobId: string) => {
    try {
      await apiRequest(`/images/sync-jobs/${jobId}`, { method: 'DELETE' });
      fetchJobs();
    } catch (err) {
      console.error('Failed to cancel job:', err);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center p-4">
        <i className="fa-solid fa-spinner fa-spin text-stone-400 mr-2" />
        <span className="text-xs text-stone-500">Loading sync jobs...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-center">
        <i className="fa-solid fa-exclamation-triangle text-red-500 mr-2" />
        <span className="text-xs text-red-500">{error}</span>
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="p-4 text-center text-xs text-stone-500">
        No sync jobs found
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {jobs.map((job) => (
        <div
          key={job.id}
          className="bg-white dark:bg-stone-900 rounded-lg border border-stone-200 dark:border-stone-800 p-3"
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                  statusColors[job.status] || statusColors.pending
                }`}
              >
                <i className={`fa-solid ${statusIcons[job.status] || statusIcons.pending}`} />
                {job.status}
              </span>
              <span className="text-xs text-stone-500 dark:text-stone-400">
                {job.host_name || job.host_id.slice(0, 8)}
              </span>
            </div>
            {['pending', 'transferring', 'loading'].includes(job.status) && (
              <button
                onClick={() => handleCancel(job.id)}
                className="text-xs text-red-500 hover:text-red-600 font-medium"
              >
                Cancel
              </button>
            )}
          </div>

          {/* Image ID */}
          <div className="text-xs font-mono text-stone-600 dark:text-stone-400 truncate mb-2">
            {job.image_id}
          </div>

          {/* Progress bar */}
          {['transferring', 'loading'].includes(job.status) && (
            <div className="mb-2">
              <div className="flex justify-between text-[10px] text-stone-500 mb-1">
                <span>{job.progress_percent}%</span>
                <span>
                  {formatBytes(job.bytes_transferred)} / {formatBytes(job.total_bytes || job.bytes_transferred)}
                </span>
              </div>
              <div className="h-1.5 bg-stone-200 dark:bg-stone-800 rounded-full overflow-hidden">
                <div
                  className={`h-full transition-all ${
                    job.status === 'loading' ? 'bg-purple-500' : 'bg-blue-500'
                  }`}
                  style={{ width: `${job.progress_percent}%` }}
                />
              </div>
            </div>
          )}

          {/* Error message */}
          {job.error_message && (
            <div className="text-xs text-red-500 bg-red-50 dark:bg-red-900/20 rounded p-2 mt-2">
              <i className="fa-solid fa-exclamation-circle mr-1" />
              {job.error_message}
            </div>
          )}

          {/* Footer stats */}
          <div className="flex items-center justify-between text-[10px] text-stone-400 mt-2">
            <span>
              <i className="fa-solid fa-clock mr-1" />
              {formatDuration(job.started_at, job.completed_at)}
            </span>
            <span className="font-mono">{job.id.slice(0, 8)}</span>
          </div>
        </div>
      ))}
    </div>
  );
};

export default ImageSyncProgress;
