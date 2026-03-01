import { useCallback, useEffect, useState } from 'react';

export interface LabSummary {
  id: string;
  name: string;
  created_at?: string;
  node_count?: number;
  running_count?: number;
  container_count?: number;
  vm_count?: number;
}

export interface SystemMetrics {
  agents: { online: number; total: number };
  containers: { running: number; total: number };
  cpu_percent: number;
  memory_percent: number;
  memory?: {
    used_gb: number;
    total_gb: number;
    percent: number;
  };
  storage?: {
    used_gb: number;
    total_gb: number;
    percent: number;
  };
  labs_running: number;
  labs_total: number;
  per_host?: {
    id: string;
    name: string;
    cpu_percent: number;
    memory_percent: number;
    memory_used_gb: number;
    memory_total_gb: number;
    storage_percent: number;
    storage_used_gb: number;
    storage_total_gb: number;
    containers_running: number;
    started_at: string | null;
  }[];
  is_multi_host?: boolean;
}

interface UseLabDataLoadingOptions {
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  activeLab: LabSummary | null;
}

export function useLabDataLoading({ studioRequest, activeLab }: UseLabDataLoadingOptions) {
  const [labs, setLabs] = useState<LabSummary[]>([]);
  const [agents, setAgents] = useState<{ id: string; name: string }[]>(() => {
    return [];
  });
  const [labStatuses, setLabStatuses] = useState<Record<string, { running: number; total: number }>>({});
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics | null>(null);

  const loadLabs = useCallback(async () => {
    const data = await studioRequest<{ labs: LabSummary[] }>('/labs');
    setLabs(data.labs || []);
  }, [studioRequest]);

  const loadAgents = useCallback(async () => {
    try {
      const data = await studioRequest<{ id: string; name: string; address: string; status: string }[]>('/agents');
      setAgents((data || []).filter((a) => a.status === 'online').map((a) => ({ id: a.id, name: a.name })));
    } catch {
      // Agents may not be available
    }
  }, [studioRequest]);

  const loadSystemMetrics = useCallback(async () => {
    try {
      const data = await studioRequest<SystemMetrics>('/dashboard/metrics');
      setSystemMetrics(data);
    } catch {
      // Metrics endpoint may fail - that's ok
    }
  }, [studioRequest]);

  const loadLabStatuses = useCallback(async (labIds: string[]) => {
    const statuses: Record<string, { running: number; total: number }> = {};
    await Promise.all(
      labIds.map(async (labId) => {
        try {
          const statusData = await studioRequest<{ nodes?: { name: string; status: string }[] }>(`/labs/${labId}/status`);
          if (statusData.nodes) {
            const running = statusData.nodes.filter((n) => n.status === 'running').length;
            statuses[labId] = { running, total: statusData.nodes.length };
          }
        } catch {
          // Lab may not be deployed - that's ok
        }
      })
    );
    setLabStatuses(statuses);
  }, [studioRequest]);

  // Initial load
  useEffect(() => {
    loadLabs();
    loadSystemMetrics();
    loadAgents();
  }, [loadLabs, loadSystemMetrics, loadAgents]);

  // Load lab statuses when labs change
  useEffect(() => {
    if (labs.length > 0 && !activeLab) {
      loadLabStatuses(labs.map((lab) => lab.id));
    }
  }, [labs, activeLab, loadLabStatuses]);

  // Poll for system metrics (both dashboard and lab views)
  useEffect(() => {
    const timer = setInterval(() => {
      loadSystemMetrics();
      // Only poll lab statuses when on dashboard
      if (!activeLab && labs.length > 0) {
        loadLabStatuses(labs.map((lab) => lab.id));
      }
    }, 10000);
    return () => clearInterval(timer);
  }, [activeLab, labs, loadSystemMetrics, loadLabStatuses]);

  return {
    labs,
    setLabs,
    agents,
    labStatuses,
    systemMetrics,
    loadLabs,
    loadAgents,
    loadSystemMetrics,
    loadLabStatuses,
  };
}
