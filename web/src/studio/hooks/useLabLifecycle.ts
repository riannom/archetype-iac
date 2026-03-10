import { useCallback, useEffect, useRef } from 'react';
import { Annotation, Node } from '../types';
import { rawApiRequest } from '../../api';
import { downloadBlob } from '../../utils/download';
import { LabSummary } from './useLabDataLoading';

interface UseLabLifecycleOptions {
  activeLab: LabSummary | null;
  setActiveLab: React.Dispatch<React.SetStateAction<LabSummary | null>>;
  labs: LabSummary[];
  setLabs: React.Dispatch<React.SetStateAction<LabSummary[]>>;
  nodes: Node[];
  annotations: Annotation[];
  setNodes: React.Dispatch<React.SetStateAction<Node[]>>;
  setLinks: React.Dispatch<React.SetStateAction<import('../types').Link[]>>;
  setAnnotations: React.Dispatch<React.SetStateAction<Annotation[]>>;
  setView: React.Dispatch<React.SetStateAction<string>>;

  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
  loadLabs: () => void;

  layoutDirtyRef: React.MutableRefObject<boolean>;
  saveLayout: (labId: string, nodes: Node[], annotations: Annotation[]) => void;

  resetJobTracking: () => void;
  clearSelection: () => void;
  consoleManager: { resetConsoles: () => void };

  // Auth
  beginLogout: () => void;
  clearUser: () => void;
  attemptLogin: (username: string, password?: string) => Promise<boolean>;
  refreshUser: () => Promise<void>;
  refreshDeviceCatalog: () => Promise<void>;

  addNotification: (...args: any[]) => void;

  // Node state sync
  wsConnected: boolean;
  refreshNodeStatesFromAgent: (labId: string) => Promise<void>;
  loadNodeStates: (labId: string, nodes: Node[]) => Promise<void>;
  loadJobs: (labId: string, nodes: Node[]) => void;
  loadNodeReadiness: (labId: string) => Promise<void>;
}

export function useLabLifecycle(options: UseLabLifecycleOptions) {
  const {
    activeLab, setActiveLab, labs, setLabs, nodes, annotations, setNodes, setLinks, setAnnotations, setView,
    studioRequest, loadLabs,
    layoutDirtyRef, saveLayout,
    resetJobTracking, clearSelection, consoleManager,
    beginLogout, clearUser, attemptLogin, refreshUser, refreshDeviceCatalog,
    addNotification,
    wsConnected, refreshNodeStatesFromAgent, loadNodeStates, loadJobs, loadNodeReadiness,
  } = options;

  const initialNodeSyncLabRef = useRef<string | null>(null);

  // On lab entry, force a deterministic sync sequence:
  // refresh from agent first, then load node states/jobs.
  useEffect(() => {
    if (!activeLab) {
      initialNodeSyncLabRef.current = null;
      return;
    }
    let cancelled = false;
    const labId = activeLab.id;
    if (initialNodeSyncLabRef.current === labId) return;
    initialNodeSyncLabRef.current = labId;

    const syncOnOpen = async () => {
      await refreshNodeStatesFromAgent(labId);
      if (cancelled) return;
      await loadNodeStates(labId, nodes);
      if (cancelled) return;
      await loadJobs(labId, nodes);
      if (cancelled) return;
      await loadNodeReadiness(labId);
    };

    void syncOnOpen();
    return () => { cancelled = true; };
  }, [activeLab, refreshNodeStatesFromAgent, loadNodeStates, loadJobs, loadNodeReadiness, nodes]);

  useEffect(() => {
    if (!activeLab || nodes.length === 0) return;
    loadNodeStates(activeLab.id, nodes);
    loadJobs(activeLab.id, nodes);
    loadNodeReadiness(activeLab.id);
  }, [activeLab, nodes, loadNodeStates, loadJobs, loadNodeReadiness]);

  // Poll for node state and job updates
  useEffect(() => {
    if (!activeLab || nodes.length === 0) return;
    const interval = wsConnected ? 15000 : 4000;
    const timer = setInterval(() => {
      loadNodeStates(activeLab.id, nodes);
      loadJobs(activeLab.id, nodes);
      loadNodeReadiness(activeLab.id);
    }, interval);
    return () => clearInterval(timer);
  }, [activeLab, nodes, loadNodeStates, loadJobs, loadNodeReadiness, wsConnected]);

  // Prevent fixed-position console windows from causing document-level horizontal scrollbar
  useEffect(() => {
    document.documentElement.style.overflowX = 'hidden';
    return () => { document.documentElement.style.overflowX = ''; };
  }, []);

  const handleCreateLab = useCallback(async () => {
    const name = `Project_${labs.length + 1}`;
    await studioRequest('/labs', { method: 'POST', body: JSON.stringify({ name }) });
    loadLabs();
  }, [labs.length, studioRequest, loadLabs]);

  const handleSelectLab = useCallback((lab: LabSummary) => {
    if (activeLab && layoutDirtyRef.current && nodes.length > 0) {
      saveLayout(activeLab.id, nodes, annotations);
    }
    resetJobTracking();
    setActiveLab(lab);
    setAnnotations([]);
    consoleManager.resetConsoles();
    clearSelection();
    setView('designer');
  }, [activeLab, layoutDirtyRef, nodes, annotations, saveLayout, resetJobTracking, setActiveLab, setAnnotations, consoleManager, clearSelection, setView]);

  const handleExitLab = useCallback(() => {
    if (!activeLab) return;
    const confirmed = window.confirm('Are you sure you want to exit the lab?');
    if (!confirmed) return;
    consoleManager.resetConsoles();
    setActiveLab(null);
  }, [activeLab, consoleManager, setActiveLab]);

  const handleLogout = useCallback(() => {
    beginLogout();
    clearUser();
    setActiveLab(null);
    consoleManager.resetConsoles();
  }, [beginLogout, clearUser, setActiveLab, consoleManager]);

  const handleDeleteLab = useCallback(async (labId: string) => {
    await studioRequest(`/labs/${labId}`, { method: 'DELETE' });
    if (activeLab?.id === labId) {
      setActiveLab(null);
      setNodes([]);
      setLinks([]);
    }
    loadLabs();
  }, [activeLab, studioRequest, loadLabs, setActiveLab, setNodes, setLinks]);

  const handleRenameLab = useCallback(async (labId: string, newName: string) => {
    await studioRequest(`/labs/${labId}`, {
      method: 'PUT',
      body: JSON.stringify({ name: newName }),
    });
    setLabs((prev) => prev.map((lab) => (lab.id === labId ? { ...lab, name: newName } : lab)));
    if (activeLab?.id === labId) {
      setActiveLab((prev) => (prev ? { ...prev, name: newName } : prev));
    }
  }, [activeLab, studioRequest, setLabs, setActiveLab]);

  const handleDownloadBundle = useCallback(async (lab: LabSummary) => {
    try {
      const response = await rawApiRequest(`/labs/${lab.id}/download-bundle`);
      if (!response.ok) {
        let errorMessage = 'Bundle download failed';
        try {
          const err = await response.json();
          if (err?.detail) {
            errorMessage = String(err.detail);
          }
        } catch {
          // Ignore parse failures and use default message.
        }
        throw new Error(errorMessage);
      }

      const blob = await response.blob();
      const filename =
        response.headers.get('Content-Disposition')?.split('filename=')[1] ||
        `${lab.name.replace(/\s+/g, '_')}_bundle.zip`;
      downloadBlob(blob, filename);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Bundle download failed';
      addNotification('error', 'Download failed', message);
      console.error('Bundle download failed:', error);
    }
  }, [addNotification]);

  const handleLogin = useCallback(async (username: string, password?: string) => {
    const success = await attemptLogin(username, password);
    if (success) {
      await refreshUser();
      await loadLabs();
      await refreshDeviceCatalog();
    }
  }, [attemptLogin, refreshUser, loadLabs, refreshDeviceCatalog]);

  return {
    handleCreateLab,
    handleSelectLab,
    handleExitLab,
    handleLogout,
    handleDeleteLab,
    handleRenameLab,
    handleDownloadBundle,
    handleLogin,
  };
}
