import { useCallback, useEffect, useState } from 'react';
import { useNotifications } from '../../contexts/NotificationContext';
import { apiRequest } from '../../api';
import type { HostDetailed, UpdateStatus } from './infrastructureTypes';

export function useAgentUpdates(
  hosts: HostDetailed[],
  loadHosts: () => Promise<void>,
) {
  const { addNotification } = useNotifications();

  const notifyError = useCallback((title: string, err: unknown) => {
    addNotification('error', title, err instanceof Error ? err.message : undefined);
  }, [addNotification]);

  const [updatingAgents, setUpdatingAgents] = useState<Set<string>>(new Set());
  const [updateStatuses, setUpdateStatuses] = useState<Map<string, UpdateStatus>>(new Map());
  const [customUpdateTarget, setCustomUpdateTarget] = useState<{ hostId: string; hostName: string } | null>(null);
  const [customVersion, setCustomVersion] = useState('');

  const removeUpdatingAgent = useCallback((hostId: string) => {
    setUpdatingAgents(prev => {
      const next = new Set(prev);
      next.delete(hostId);
      return next;
    });
  }, []);

  // Poll update status for agents being updated
  useEffect(() => {
    if (updatingAgents.size === 0) return;

    const pollInterval = setInterval(async () => {
      for (const agentId of updatingAgents) {
        try {
          const status = await apiRequest<UpdateStatus | null>(`/agents/${agentId}/update-status`);
          if (status) {
            if (status.status === 'restarting') {
              const host = hosts.find(h => h.id === agentId);
              if (host && host.status === 'online' && host.version === status.to_version) {
                setUpdateStatuses(prev => new Map(prev).set(agentId, {
                  ...status,
                  status: 'completed',
                  progress_percent: 100
                }));
                removeUpdatingAgent(agentId);
                loadHosts();
                continue;
              }
            }

            setUpdateStatuses(prev => new Map(prev).set(agentId, status));

            if (status.status === 'completed' || status.status === 'failed') {
              removeUpdatingAgent(agentId);
              if (status.status === 'completed') {
                loadHosts();
              }
            }
          }
        } catch (err) {
          console.error(`Failed to poll update status for ${agentId}:`, err);
        }
      }
    }, 2000);

    return () => clearInterval(pollInterval);
  }, [updatingAgents, loadHosts, hosts, removeUpdatingAgent]);

  const triggerUpdate = async (hostId: string, targetVersion?: string) => {
    try {
      setUpdatingAgents(prev => new Set(prev).add(hostId));
      const response = await apiRequest<{ job_id: string; status: string; message: string }>(
        `/agents/${hostId}/update`,
        {
          method: 'POST',
          ...(targetVersion ? { body: JSON.stringify({ target_version: targetVersion }) } : {}),
        }
      );

      if (response.status === 'failed') {
        addNotification('error', 'Update failed to start', response.message || undefined);
        removeUpdatingAgent(hostId);
      }
    } catch (err) {
      console.error('Failed to trigger update:', err);
      notifyError('Failed to trigger update', err);
      removeUpdatingAgent(hostId);
    }
  };

  const triggerRebuild = async (hostId: string) => {
    if (!confirm('Rebuild the agent container? This will restart the agent with the latest code.')) {
      return;
    }

    try {
      setUpdatingAgents(prev => new Set(prev).add(hostId));
      const response = await apiRequest<{ success: boolean; message: string; output?: string }>(
        `/agents/${hostId}/rebuild`,
        { method: 'POST' }
      );

      if (response.success) {
        setTimeout(() => {
          removeUpdatingAgent(hostId);
          loadHosts();
        }, 5000);
      } else {
        addNotification('error', 'Rebuild failed', response.message || undefined);
        removeUpdatingAgent(hostId);
      }
    } catch (err) {
      console.error('Failed to trigger rebuild:', err);
      notifyError('Failed to trigger rebuild', err);
      removeUpdatingAgent(hostId);
    }
  };

  const triggerBulkUpdate = async (latestVersion: string) => {
    const outdatedAgents = hosts.filter(
      h => h.status === 'online' && h.version && h.version !== latestVersion
    );

    if (outdatedAgents.length === 0) {
      addNotification('info', 'All agents are already up to date');
      return;
    }

    if (!confirm(`Update ${outdatedAgents.length} agent(s) to version ${latestVersion}?`)) {
      return;
    }

    try {
      const agentIds = outdatedAgents.map(h => h.id);
      setUpdatingAgents(prev => {
        const next = new Set(prev);
        agentIds.forEach(id => next.add(id));
        return next;
      });

      const response = await apiRequest<{
        success_count: number;
        failure_count: number;
        results: Array<{ agent_id: string; success: boolean; error?: string }>;
      }>('/agents/updates/bulk', {
        method: 'POST',
        body: JSON.stringify({ agent_ids: agentIds }),
      });

      if (response.failure_count > 0) {
        const failures = response.results
          .filter(r => !r.success)
          .map(r => `${r.agent_id}: ${r.error}`)
          .join('\n');
        addNotification(
          'warning',
          'Bulk update partially failed',
          `${response.success_count} updates started, ${response.failure_count} failed:\n${failures}`
        );
      }

      response.results.filter(r => !r.success).forEach(r => {
        removeUpdatingAgent(r.agent_id);
      });
    } catch (err) {
      console.error('Failed to trigger bulk update:', err);
      notifyError('Failed to trigger bulk update', err);
      setUpdatingAgents(new Set());
    }
  };

  const isUpdateAvailable = (host: HostDetailed, latestVersion: string): boolean => {
    if (!latestVersion || !host.version) return false;
    return host.version !== latestVersion;
  };

  return {
    updatingAgents,
    updateStatuses,
    customUpdateTarget,
    setCustomUpdateTarget,
    customVersion,
    setCustomVersion,
    triggerUpdate,
    triggerRebuild,
    triggerBulkUpdate,
    isUpdateAvailable,
    removeUpdatingAgent,
  };
}
