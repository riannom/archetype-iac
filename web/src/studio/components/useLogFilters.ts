import { useCallback, useMemo, useState } from 'react';
import { LabLogsQueryParams } from '../../api';

export interface LogFiltersState {
  selectedJobId: string;
  setSelectedJobId: (id: string) => void;
  selectedHostId: string;
  setSelectedHostId: (id: string) => void;
  selectedLevel: string;
  setSelectedLevel: (level: string) => void;
  selectedSince: string;
  setSelectedSince: (since: string) => void;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  queryParams: LabLogsQueryParams;
  hasActiveFilters: boolean;
  clearFilters: () => void;
}

export function useLogFilters(): LogFiltersState {
  const [selectedJobId, setSelectedJobId] = useState<string>('all');
  const [selectedHostId, setSelectedHostId] = useState<string>('all');
  const [selectedLevel, setSelectedLevel] = useState<string>('all');
  const [selectedSince, setSelectedSince] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');

  const queryParams = useMemo((): LabLogsQueryParams => {
    const params: LabLogsQueryParams = {};
    if (selectedJobId !== 'all') params.job_id = selectedJobId;
    if (selectedHostId !== 'all') params.host_id = selectedHostId;
    if (selectedLevel !== 'all') params.level = selectedLevel;
    if (selectedSince !== 'all') params.since = selectedSince;
    if (searchQuery.trim()) params.search = searchQuery.trim();
    params.limit = 500;
    return params;
  }, [selectedJobId, selectedHostId, selectedLevel, selectedSince, searchQuery]);

  const hasActiveFilters =
    selectedJobId !== 'all' ||
    selectedHostId !== 'all' ||
    selectedLevel !== 'all' ||
    selectedSince !== 'all' ||
    searchQuery.trim() !== '';

  const clearFilters = useCallback(() => {
    setSelectedJobId('all');
    setSelectedHostId('all');
    setSelectedLevel('all');
    setSelectedSince('all');
    setSearchQuery('');
  }, []);

  return {
    selectedJobId,
    setSelectedJobId,
    selectedHostId,
    setSelectedHostId,
    selectedLevel,
    setSelectedLevel,
    selectedSince,
    setSelectedSince,
    searchQuery,
    setSearchQuery,
    queryParams,
    hasActiveFilters,
    clearFilters,
  };
}
