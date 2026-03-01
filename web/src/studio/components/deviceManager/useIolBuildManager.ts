import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiRequest } from '../../../api';
import { ImageLibraryEntry } from '../../types';
import { usePersistedState } from '../../hooks/usePersistedState';
import { usePolling } from '../../hooks/usePolling';
import {
  IolBuildStatusResponse,
  IolBuildDiagnosticsResponse,
  IolBuildRow,
} from './deviceManagerTypes';
import { normalizeBuildStatus, normalizeBuildStatusError } from './deviceManagerUtils';

interface UseIolBuildManagerArgs {
  imageLibrary: ImageLibraryEntry[];
  isBuildJobsMode: boolean;
  onRefresh: () => void;
  setUploadStatus: (status: string | null) => void;
}

export function useIolBuildManager({
  imageLibrary,
  isBuildJobsMode,
  onRefresh,
  setUploadStatus,
}: UseIolBuildManagerArgs) {
  const completedIolBuildsRef = useRef<Set<string>>(new Set());
  const [iolBuildStatuses, setIolBuildStatuses] = useState<Record<string, IolBuildStatusResponse>>({});
  const [refreshingIolBuilds, setRefreshingIolBuilds] = useState(false);
  const [retryingIolImageId, setRetryingIolImageId] = useState<string | null>(null);
  const [ignoringIolImageId, setIgnoringIolImageId] = useState<string | null>(null);
  const [showIolDiagnostics, setShowIolDiagnostics] = useState(false);
  const [iolDiagnostics, setIolDiagnostics] = useState<IolBuildDiagnosticsResponse | null>(null);
  const [iolDiagnosticsLoading, setIolDiagnosticsLoading] = useState(false);
  const [iolDiagnosticsError, setIolDiagnosticsError] = useState<string | null>(null);
  const [autoRefreshIolBuilds, setAutoRefreshIolBuilds] = usePersistedState<boolean>(
    'archetype:iol-build:auto-refresh',
    true
  );

  const iolSourceImages = useMemo(
    () => imageLibrary.filter((img) => (img.kind || '').toLowerCase() === 'iol'),
    [imageLibrary]
  );

  const builtDockerBySource = useMemo(() => {
    const map = new Map<string, ImageLibraryEntry>();
    imageLibrary.forEach((img) => {
      if ((img.kind || '').toLowerCase() !== 'docker') return;
      if (!img.built_from) return;
      map.set(img.built_from, img);
    });
    return map;
  }, [imageLibrary]);

  useEffect(() => {
    iolSourceImages.forEach((img) => {
      if (builtDockerBySource.has(img.id)) {
        completedIolBuildsRef.current.add(img.id);
      }
    });
  }, [iolSourceImages, builtDockerBySource]);

  const refreshIolBuildStatuses = useCallback(async () => {
    if (iolSourceImages.length === 0) {
      setIolBuildStatuses({});
      return;
    }
    setRefreshingIolBuilds(true);
    try {
      const entries = await Promise.all(
        iolSourceImages.map(async (img) => {
          try {
            const data = await apiRequest<IolBuildStatusResponse>(
              `/images/library/${encodeURIComponent(img.id)}/build-status`
            );
            return [img.id, data, null] as const;
          } catch (error) {
            return [img.id, null, normalizeBuildStatusError(error)] as const;
          }
        })
      );
      setIolBuildStatuses((prev) => {
        const next: Record<string, IolBuildStatusResponse> = {};
        const validIds = new Set(iolSourceImages.map((img) => img.id));

        Object.entries(prev).forEach(([id, value]) => {
          if (validIds.has(id)) next[id] = value;
        });

        entries.forEach(([imageId, data, fetchError]) => {
          if (data) {
            next[imageId] = data;
            return;
          }
          if (fetchError) {
            next[imageId] = {
              ...(next[imageId] || {}),
              build_error: fetchError,
            };
          }
        });

        return next;
      });

      const newlyCompletedIds = entries
        .filter(([, status]) => !!status && normalizeBuildStatus(status.status || status.build_status) === 'complete')
        .map(([imageId]) => imageId)
        .filter((imageId) => !completedIolBuildsRef.current.has(imageId));

      if (newlyCompletedIds.length > 0) {
        newlyCompletedIds.forEach((imageId) => completedIolBuildsRef.current.add(imageId));
        onRefresh();
      }
    } finally {
      setRefreshingIolBuilds(false);
    }
  }, [iolSourceImages, onRefresh]);

  const retryIolBuild = useCallback(async (imageId: string, forceRebuild: boolean = false) => {
    setRetryingIolImageId(imageId);
    try {
      await apiRequest<{ build_status: string; build_job_id: string }>(
        `/images/library/${encodeURIComponent(imageId)}/retry-build?force_rebuild=${forceRebuild}`,
        { method: 'POST' }
      );
      setUploadStatus(forceRebuild ? 'Forced IOL rebuild queued.' : 'IOL build retry queued.');
      await refreshIolBuildStatuses();
      onRefresh();
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'Failed to retry IOL build');
    } finally {
      setRetryingIolImageId(null);
    }
  }, [onRefresh, refreshIolBuildStatuses, setUploadStatus]);

  const ignoreIolBuildFailure = useCallback(async (imageId: string) => {
    setIgnoringIolImageId(imageId);
    try {
      await apiRequest<{ build_status: string }>(
        `/images/library/${encodeURIComponent(imageId)}/ignore-build-failure`,
        { method: 'POST' }
      );
      setUploadStatus('IOL build failure ignored.');
      await refreshIolBuildStatuses();
      onRefresh();
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'Failed to ignore IOL build failure');
    } finally {
      setIgnoringIolImageId(null);
    }
  }, [onRefresh, refreshIolBuildStatuses, setUploadStatus]);

  const openIolDiagnostics = useCallback(async (imageId: string) => {
    setShowIolDiagnostics(true);
    setIolDiagnosticsLoading(true);
    setIolDiagnosticsError(null);
    setIolDiagnostics(null);
    try {
      const details = await apiRequest<IolBuildDiagnosticsResponse>(
        `/images/library/${encodeURIComponent(imageId)}/build-diagnostics`
      );
      setIolDiagnostics(details);
    } catch (error) {
      setIolDiagnosticsError(error instanceof Error ? error.message : 'Failed to load build diagnostics');
    } finally {
      setIolDiagnosticsLoading(false);
    }
  }, []);

  const iolBuildRows: IolBuildRow[] = useMemo(() => {
    return iolSourceImages.map((img) => {
      const liveStatus = iolBuildStatuses[img.id];
      const builtDocker = builtDockerBySource.get(img.id);
      const effectiveStatus = normalizeBuildStatus(
        liveStatus?.status || liveStatus?.build_status || (builtDocker ? 'complete' : img.build_status)
      );
      return {
        image: img,
        status: effectiveStatus,
        buildError: liveStatus?.build_error || img.build_error || null,
        buildJobId: liveStatus?.build_job_id || img.build_job_id || null,
        buildIgnoredAt: liveStatus?.build_ignored_at || img.build_ignored_at || null,
        buildIgnoredBy: liveStatus?.build_ignored_by || img.build_ignored_by || null,
        dockerReference: liveStatus?.docker_reference || builtDocker?.reference || null,
        dockerImageId: liveStatus?.docker_image_id || builtDocker?.id || null,
      };
    });
  }, [iolSourceImages, iolBuildStatuses, builtDockerBySource]);

  const hasActiveIolBuilds = useMemo(
    () => iolBuildRows.some((row) => row.status === 'queued' || row.status === 'building'),
    [iolBuildRows]
  );
  const activeIolBuildCount = useMemo(
    () => iolBuildRows.filter((row) => row.status === 'queued' || row.status === 'building').length,
    [iolBuildRows]
  );
  const currentIolBuildRows = useMemo(
    () => iolBuildRows.filter((row) => row.status !== 'complete'),
    [iolBuildRows]
  );
  const historicalIolBuildRows = useMemo(
    () => iolBuildRows.filter((row) => row.status === 'complete'),
    [iolBuildRows]
  );

  useEffect(() => {
    if (!isBuildJobsMode) {
      setIolBuildStatuses({});
      return;
    }
    if (iolSourceImages.length === 0) {
      setIolBuildStatuses({});
      return;
    }
    void refreshIolBuildStatuses();
  }, [isBuildJobsMode, iolSourceImages, refreshIolBuildStatuses]);

  usePolling(refreshIolBuildStatuses, 5000, isBuildJobsMode && autoRefreshIolBuilds && hasActiveIolBuilds);

  return {
    iolBuildRows,
    hasActiveIolBuilds,
    activeIolBuildCount,
    currentIolBuildRows,
    historicalIolBuildRows,
    refreshingIolBuilds,
    retryingIolImageId,
    ignoringIolImageId,
    showIolDiagnostics,
    setShowIolDiagnostics,
    iolDiagnostics,
    iolDiagnosticsLoading,
    iolDiagnosticsError,
    autoRefreshIolBuilds,
    setAutoRefreshIolBuilds,
    refreshIolBuildStatuses,
    retryIolBuild,
    ignoreIolBuildFailure,
    openIolDiagnostics,
  };
}
