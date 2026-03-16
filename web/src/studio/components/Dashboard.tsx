
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../../theme/index';
import { useUser } from '../../contexts/UserContext';
import { canViewInfrastructure } from '../../utils/permissions';
import { useModalState } from '../../hooks/useModalState';
import SystemStatusStrip from './SystemStatusStrip';
import SystemLogsModal from './SystemLogsModal';
import { ArchetypeIcon } from '../../components/icons';
import { VersionBadge } from '../../components/VersionBadge';
import AdminMenuButton from '../../components/AdminMenuButton';
import { EmptyState } from '../../components/ui/EmptyState';
import { Select } from '../../components/ui/Select';
import { Tooltip } from '../../components/ui/Tooltip';
import type { SystemMetrics } from '../types';

interface LabSummary {
  id: string;
  name: string;
  created_at?: string;
  node_count?: number;
  running_count?: number;
  container_count?: number;
  vm_count?: number;
}

interface LabStatus {
  running: number;
  total: number;
}

interface DashboardProps {
  labs: LabSummary[];
  labStatuses?: Record<string, LabStatus>;
  systemMetrics?: SystemMetrics | null;
  onSelect: (lab: LabSummary) => void;
  onDownload?: (lab: LabSummary) => void;
  onCreate: () => void;
  onDelete: (labId: string) => void;
  onRename?: (labId: string, newName: string) => void;
  onLogout: () => void;
}

type LabListFilter = 'all' | 'running' | 'stopped';
type LabSortOption =
  | 'created_desc'
  | 'created_asc'
  | 'name_asc'
  | 'name_desc'
  | 'nodes_desc'
  | 'nodes_asc';

const LABS_PER_PAGE = 9;
const SORT_OPTIONS: LabSortOption[] = [
  'created_desc',
  'created_asc',
  'name_asc',
  'name_desc',
  'nodes_desc',
  'nodes_asc',
];

const parseLabListFilter = (value: string | null): LabListFilter => {
  if (value === 'running' || value === 'stopped') {
    return value;
  }
  return 'all';
};

const parseLabSortOption = (value: string | null): LabSortOption => {
  if (value && SORT_OPTIONS.includes(value as LabSortOption)) {
    return value as LabSortOption;
  }
  return 'created_desc';
};

const parsePage = (value: string | null): number => {
  const parsed = Number(value);
  if (Number.isFinite(parsed) && parsed >= 1) {
    return Math.floor(parsed);
  }
  return 1;
};

const Dashboard: React.FC<DashboardProps> = ({
  labs,
  labStatuses,
  systemMetrics,
  onSelect,
  onDownload,
  onCreate,
  onDelete,
  onRename,
  onLogout,
}) => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user } = useUser();
  const [searchParams, setSearchParams] = useSearchParams();
  const [showThemeSelector, setShowThemeSelector] = useState(false);
  const systemLogsModal = useModalState();
  const [editingLabId, setEditingLabId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const deleteTimeoutRef = useRef<number | null>(null);
  const showInfra = canViewInfrastructure(user ?? null);
  const searchQuery = searchParams.get('q') ?? '';
  const listFilter = parseLabListFilter(searchParams.get('status'));
  const sortOption = parseLabSortOption(searchParams.get('sort'));
  const page = parsePage(searchParams.get('page'));

  const updateDashboardParams = useCallback((updates: {
    q?: string;
    status?: LabListFilter;
    sort?: LabSortOption;
    page?: number;
  }) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);

      if (updates.q !== undefined) {
        const trimmed = updates.q.trim();
        if (trimmed) {
          next.set('q', trimmed);
        } else {
          next.delete('q');
        }
      }

      if (updates.status !== undefined) {
        if (updates.status === 'all') {
          next.delete('status');
        } else {
          next.set('status', updates.status);
        }
      }

      if (updates.sort !== undefined) {
        if (updates.sort === 'created_desc') {
          next.delete('sort');
        } else {
          next.set('sort', updates.sort);
        }
      }

      if (updates.page !== undefined) {
        if (updates.page <= 1) {
          next.delete('page');
        } else {
          next.set('page', String(updates.page));
        }
      }

      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const filteredAndSortedLabs = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    const filtered = labs.filter((lab) => {
      const agentStatus = labStatuses?.[lab.id];
      const running = agentStatus ? agentStatus.running : (lab.running_count ?? 0);
      const matchesFilter =
        listFilter === 'all'
          ? true
          : listFilter === 'running'
            ? running > 0
            : running === 0;
      const matchesQuery =
        normalizedQuery.length === 0 || lab.name.toLowerCase().includes(normalizedQuery);
      return matchesFilter && matchesQuery;
    });

    const sortable = [...filtered];
    sortable.sort((a, b) => {
      const aCreated = a.created_at ? Date.parse(a.created_at) : 0;
      const bCreated = b.created_at ? Date.parse(b.created_at) : 0;
      const aNodes = a.node_count ?? 0;
      const bNodes = b.node_count ?? 0;
      const aName = a.name.toLowerCase();
      const bName = b.name.toLowerCase();
      switch (sortOption) {
        case 'created_asc':
          return aCreated - bCreated;
        case 'name_asc':
          return aName.localeCompare(bName);
        case 'name_desc':
          return bName.localeCompare(aName);
        case 'nodes_desc':
          return bNodes - aNodes;
        case 'nodes_asc':
          return aNodes - bNodes;
        case 'created_desc':
        default:
          return bCreated - aCreated;
      }
    });
    return sortable;
  }, [labs, labStatuses, listFilter, searchQuery, sortOption]);

  const totalPages = Math.max(1, Math.ceil(filteredAndSortedLabs.length / LABS_PER_PAGE));
  const currentPage = Math.min(page, totalPages);

  useEffect(() => {
    if (page !== currentPage) {
      updateDashboardParams({ page: currentPage });
    }
  }, [page, currentPage, updateDashboardParams]);

  const startIndex = (currentPage - 1) * LABS_PER_PAGE;
  const pagedLabs = filteredAndSortedLabs.slice(startIndex, startIndex + LABS_PER_PAGE);

  useEffect(() => {
    return () => {
      if (deleteTimeoutRef.current) {
        window.clearTimeout(deleteTimeoutRef.current);
      }
    };
  }, []);

  const handleStartEdit = (lab: LabSummary, e: React.MouseEvent) => {
    e.stopPropagation();
    if (onRename) {
      setEditingLabId(lab.id);
      setEditName(lab.name);
    }
  };

  const handleSaveEdit = (labId: string) => {
    const trimmed = editName.trim();
    const lab = labs.find(l => l.id === labId);
    if (trimmed && lab && trimmed !== lab.name && onRename) {
      onRename(labId, trimmed);
    }
    setEditingLabId(null);
    setEditName('');
  };

  const handleKeyDown = (e: React.KeyboardEvent, labId: string) => {
    if (e.key === 'Enter') {
      handleSaveEdit(labId);
    } else if (e.key === 'Escape') {
      setEditingLabId(null);
      setEditName('');
    }
  };

  const handleDeleteRequest = (labId: string) => {
    if (pendingDeleteId === labId) {
      setPendingDeleteId(null);
      if (deleteTimeoutRef.current) {
        window.clearTimeout(deleteTimeoutRef.current);
      }
      onDelete(labId);
      return;
    }

    setPendingDeleteId(labId);
    if (deleteTimeoutRef.current) {
      window.clearTimeout(deleteTimeoutRef.current);
    }
    deleteTimeoutRef.current = window.setTimeout(() => {
      setPendingDeleteId(null);
    }, 3000);
  };

  const handleDeleteCancel = () => {
    setPendingDeleteId(null);
    if (deleteTimeoutRef.current) {
      window.clearTimeout(deleteTimeoutRef.current);
    }
  };

  return (
    <>
    <div className="min-h-screen bg-gradient-to-br from-stone-50/30 via-white/20 to-stone-100/30 dark:from-stone-950/30 dark:via-stone-900/20 dark:to-stone-950/30 flex flex-col overflow-hidden">
      <header className="relative z-10 h-20 border-b border-stone-200 dark:border-stone-800 glass-surface flex items-center justify-between px-10">
        <div className="flex items-center gap-4">
          <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
          <div>
            <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
            <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Network Studio</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {showInfra && <AdminMenuButton />}

          {showInfra && (
            <>
              <Tooltip content="View System Logs">
                <button
                  onClick={() => systemLogsModal.open()}
                  className="flex items-center gap-2 px-3 py-2 glass-control text-stone-600 dark:text-stone-300 rounded-lg transition-all"
                  title="View System Logs"
                >
                  <i className="fa-solid fa-file-lines text-xs"></i>
                  <span className="text-[10px] font-bold uppercase">Logs</span>
                </button>
              </Tooltip>
            </>
          )}

          <Tooltip content="Theme Settings">
            <button
              onClick={() => setShowThemeSelector(true)}
              className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all"
              title="Theme Settings"
            >
              <i className="fa-solid fa-palette text-sm"></i>
            </button>
          </Tooltip>

          <Tooltip content={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}>
            <button
              onClick={toggleMode}
              className="w-9 h-9 flex items-center justify-center glass-control text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all"
              title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
            >
              <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
            </button>
          </Tooltip>

          <Tooltip content="Logout">
            <button
              onClick={onLogout}
              className="flex items-center gap-2 px-3 py-2 text-stone-500 hover:text-red-500 dark:text-stone-400 dark:hover:text-red-400 text-xs font-bold transition-all"
              title="Logout"
            >
              <i className="fa-solid fa-right-from-bracket text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Logout</span>
            </button>
          </Tooltip>

        </div>
      </header>

      {showInfra && <SystemStatusStrip metrics={systemMetrics || null} />}

      <main className="flex-1 overflow-y-auto p-10 custom-scrollbar">
        <div className="max-w-6xl mx-auto">
          <div className="flex justify-between items-center mb-8">
            <div>
              <h2 className="text-2xl font-bold text-stone-900 dark:text-white">Your Workspace</h2>
              <p className="text-stone-500 text-sm mt-1">Manage, design and deploy your virtual network environments.</p>
            </div>
            <button
              onClick={onCreate}
              className="bg-sage-600 hover:bg-sage-500 text-white px-6 py-2.5 rounded-xl font-bold text-sm shadow-lg shadow-sage-900/20 transition-all flex items-center gap-2 active:scale-95"
            >
              <i className="fa-solid fa-plus"></i>
              Create New Lab
            </button>
          </div>

          <div className="mb-6 grid grid-cols-1 lg:grid-cols-12 gap-3">
            <div className="lg:col-span-5 relative">
              <i className="fa-solid fa-search absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-xs"></i>
              <input
                aria-label="Search labs"
                type="text"
                value={searchQuery}
                onChange={(e) => updateDashboardParams({ q: e.target.value, page: 1 })}
                placeholder="Search labs..."
                className="w-full pl-9 pr-3 py-2.5 rounded-xl glass-control border text-sm text-stone-700 dark:text-stone-200 placeholder:text-stone-400"
              />
            </div>
            <div className="lg:col-span-2">
              <Select
                aria-label="Filter labs"
                value={listFilter}
                onChange={(e) => updateDashboardParams({ status: e.target.value as LabListFilter, page: 1 })}
                className="glass-control rounded-xl"
              >
                <option value="all" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">All Labs</option>
                <option value="running" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">Running</option>
                <option value="stopped" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">Stopped</option>
              </Select>
            </div>
            <div className="lg:col-span-3">
              <Select
                aria-label="Sort labs"
                value={sortOption}
                onChange={(e) => updateDashboardParams({ sort: e.target.value as LabSortOption, page: 1 })}
                className="glass-control rounded-xl"
              >
                <option value="created_desc" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">Newest First</option>
                <option value="created_asc" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">Oldest First</option>
                <option value="name_asc" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">Name A-Z</option>
                <option value="name_desc" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">Name Z-A</option>
                <option value="nodes_desc" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">Most Nodes</option>
                <option value="nodes_asc" className="bg-white text-stone-700 dark:bg-stone-900 dark:text-stone-200">Least Nodes</option>
              </Select>
            </div>
            <div className="lg:col-span-2 flex items-center justify-between gap-1.5">
              <Tooltip content="Previous page">
                <button
                  onClick={() => updateDashboardParams({ page: Math.max(1, currentPage - 1) })}
                  disabled={currentPage <= 1}
                  className="w-9 h-9 rounded-lg glass-control border text-xs font-bold text-stone-700 dark:text-stone-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-150 flex items-center justify-center"
                  title="Previous page"
                >
                  <i className="fa-solid fa-chevron-left"></i>
                </button>
              </Tooltip>
              <span className="text-xs font-semibold text-stone-600 dark:text-stone-400 tabular-nums">
                {currentPage}/{totalPages}
              </span>
              <Tooltip content="Next page">
                <button
                  onClick={() => updateDashboardParams({ page: Math.min(totalPages, currentPage + 1) })}
                  disabled={currentPage >= totalPages}
                  className="w-9 h-9 rounded-lg glass-control border text-xs font-bold text-stone-700 dark:text-stone-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-150 flex items-center justify-center"
                  title="Next page"
                >
                  <i className="fa-solid fa-chevron-right"></i>
                </button>
              </Tooltip>
            </div>
          </div>

          <div className="mb-5 flex flex-wrap items-center justify-between gap-2 text-xs text-stone-500 dark:text-stone-400">
            <span className="font-semibold">Total Labs: {labs.length}</span>
            <span>
              Showing {filteredAndSortedLabs.length === 0 ? 0 : startIndex + 1}
              -
              {Math.min(startIndex + LABS_PER_PAGE, filteredAndSortedLabs.length)} of {filteredAndSortedLabs.length}
              {' '}({currentPage}/{totalPages})
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {labs.length === 0 ? (
              <div className="col-span-full">
                <EmptyState
                  icon="fa-solid fa-diagram-project"
                  title="Empty Workspace"
                  description="Start your first journey by creating a new network lab."
                  action={{
                    label: 'Create New Lab',
                    onClick: onCreate,
                    icon: 'fa-solid fa-plus',
                  }}
                />
              </div>
            ) : filteredAndSortedLabs.length > 0 ? pagedLabs.map((lab) => {
              const agentStatus = labStatuses?.[lab.id];
              // Use agent-polled running count when available, DB total always
              // (agents only report deployed nodes, not the full topology)
              const running = agentStatus ? agentStatus.running : (lab.running_count ?? 0);
              const total = lab.node_count ?? 0;
              const isRunning = running > 0;
              const isAllRunning = running === total && total > 0;
              const statusDotColor = isAllRunning ? 'bg-green-500' : isRunning ? 'bg-amber-500' : 'bg-stone-400 dark:bg-stone-600';

              return (
              <div
                key={lab.id}
                className="group relative glass-surface border rounded-2xl p-6 hover:border-sage-500/50 hover:shadow-2xl hover:shadow-sage-900/10 transition-all duration-200 cursor-default overflow-hidden hover:-translate-y-0.5"
              >
                <div className="absolute top-0 right-0 p-4 opacity-0 group-hover:opacity-100 transition-opacity">
                  {pendingDeleteId === lab.id ? (
                    <div className="flex items-center gap-1">
                      <Tooltip content="Confirm delete">
                        <button
                          onClick={(e) => { e.stopPropagation(); handleDeleteRequest(lab.id); }}
                          className="w-8 h-8 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-all border border-red-500/20"
                          title="Confirm delete"
                        >
                          <i className="fa-solid fa-check text-xs"></i>
                        </button>
                      </Tooltip>
                      <Tooltip content="Cancel">
                        <button
                          onClick={(e) => { e.stopPropagation(); handleDeleteCancel(); }}
                          className="w-8 h-8 rounded-lg glass-control text-stone-500 transition-all"
                          title="Cancel"
                        >
                          <i className="fa-solid fa-xmark text-xs"></i>
                        </button>
                      </Tooltip>
                    </div>
                  ) : (
                    <Tooltip content="Delete lab">
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteRequest(lab.id); }}
                        className="w-8 h-8 rounded-lg bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white transition-all border border-red-500/20"
                        title="Delete lab"
                      >
                        <i className="fa-solid fa-trash-can text-xs"></i>
                      </button>
                    </Tooltip>
                  )}
                </div>

                <div className="w-12 h-12 bg-stone-100 dark:bg-stone-800 rounded-xl flex items-center justify-center mb-4 text-stone-500 dark:text-stone-400 group-hover:bg-sage-600 group-hover:text-white transition-all border border-stone-200 dark:border-stone-700">
                  <i className="fa-solid fa-diagram-project"></i>
                </div>

                {editingLabId === lab.id ? (
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onBlur={() => handleSaveEdit(lab.id)}
                    onKeyDown={(e) => handleKeyDown(e, lab.id)}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                    className="text-lg font-bold text-stone-900 dark:text-white mb-1 bg-transparent border-b-2 border-sage-500 outline-none w-full"
                  />
                ) : (
                  <h3
                    onClick={(e) => handleStartEdit(lab, e)}
                    className={`text-lg font-bold text-stone-900 dark:text-white mb-1 group-hover:text-sage-600 dark:group-hover:text-sage-400 transition-colors ${onRename ? 'cursor-text hover:bg-stone-100 dark:hover:bg-stone-800 -mx-1 px-1 rounded' : ''}`}
                    title={onRename ? "Click to rename" : undefined}
                  >
                    {lab.name}
                  </h3>
                )}
                <div className="flex items-center gap-4 text-[10px] font-bold text-stone-500 uppercase tracking-wider mb-3">
                   <span className="flex items-center gap-1.5"><i className="fa-solid fa-server"></i> Lab</span>
                   <span className="flex items-center gap-1.5"><i className="fa-solid fa-calendar"></i> {lab.created_at ? new Date(lab.created_at).toLocaleDateString() : 'New'}</span>
                </div>

                {total > 0 && (
                  <div className="mb-4 space-y-1">
                    <div className="flex items-center gap-2">
                      <div className={`w-2 h-2 rounded-full ${statusDotColor} ${isAllRunning ? 'animate-pulse' : ''}`}></div>
                      <span className="text-xs text-stone-600 dark:text-stone-400">
                        <span className="font-bold">{running}</span>
                        <span className="text-stone-400 dark:text-stone-500">/{total}</span>
                        <span className="ml-1 text-stone-500 dark:text-stone-500">nodes running</span>
                      </span>
                    </div>
                    {((lab.container_count ?? 0) > 0 || (lab.vm_count ?? 0) > 0) && (
                      <div className="flex items-center gap-2 ml-4 text-[10px] text-stone-400 dark:text-stone-500">
                        {(lab.container_count ?? 0) > 0 && (
                          <span className="flex items-center gap-1">
                            <i className="fa-solid fa-cube"></i>
                            {lab.container_count} container{lab.container_count !== 1 ? 's' : ''}
                          </span>
                        )}
                        {(lab.vm_count ?? 0) > 0 && (
                          <span className="flex items-center gap-1">
                            <i className="fa-solid fa-server"></i>
                            {lab.vm_count} VM{lab.vm_count !== 1 ? 's' : ''}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                )}

                <div className="flex gap-2">
                  <button
                    onClick={() => onSelect(lab)}
                    className="flex-1 py-2 glass-control text-stone-700 dark:text-stone-200 text-xs font-bold rounded-lg border transition-all duration-150 hover:border-sage-500/50 hover:text-sage-700 dark:hover:text-sage-400 active:scale-[0.97]"
                  >
                    Open Designer
                  </button>
                  <Tooltip content="Download lab bundle">
                    <button
                      onClick={() => onDownload?.(lab)}
                      className="w-10 py-2 glass-control text-stone-700 dark:text-stone-200 text-xs font-bold rounded-lg border transition-all duration-150 hover:border-sage-500/50 hover:text-sage-700 dark:hover:text-sage-400 active:scale-[0.97] flex items-center justify-center"
                      title="Download lab bundle"
                    >
                      <i className="fa-solid fa-download"></i>
                    </button>
                  </Tooltip>
                </div>
              </div>
              );
            }) : (
              <div className="col-span-full">
                <EmptyState
                  icon="fa-solid fa-magnifying-glass"
                  title="No Matching Labs"
                  description="Adjust your search, filter, or sort options."
                />
              </div>
            )}
          </div>
        </div>
      </main>

      <footer className="h-10 border-t border-stone-200 dark:border-stone-900 glass-surface flex items-center px-10 justify-between text-[10px] text-stone-500 dark:text-stone-500 font-medium">
        <span>© 2026 Archetype Network Studio</span>
        <VersionBadge />
      </footer>
    </div>

    <ThemeSelector
      isOpen={showThemeSelector}
      onClose={() => setShowThemeSelector(false)}
    />

    <SystemLogsModal
      isOpen={systemLogsModal.isOpen}
      onClose={systemLogsModal.close}
    />
    </>
  );
};

export default Dashboard;
