import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useLogFilters } from './useLogFilters';

describe('useLogFilters', () => {
  it('starts with all filters set to "all" / empty', () => {
    const { result } = renderHook(() => useLogFilters());
    expect(result.current.selectedJobId).toBe('all');
    expect(result.current.selectedHostId).toBe('all');
    expect(result.current.selectedLevel).toBe('all');
    expect(result.current.selectedSince).toBe('all');
    expect(result.current.searchQuery).toBe('');
    expect(result.current.hasActiveFilters).toBe(false);
  });

  it('emits a default queryParams with only the limit', () => {
    const { result } = renderHook(() => useLogFilters());
    expect(result.current.queryParams).toEqual({ limit: 500 });
  });

  it('includes job_id, host_id, level, since when not "all"', () => {
    const { result } = renderHook(() => useLogFilters());
    act(() => {
      result.current.setSelectedJobId('job-42');
      result.current.setSelectedHostId('host-1');
      result.current.setSelectedLevel('error');
      result.current.setSelectedSince('15m');
    });
    expect(result.current.queryParams).toEqual({
      job_id: 'job-42',
      host_id: 'host-1',
      level: 'error',
      since: '15m',
      limit: 500,
    });
    expect(result.current.hasActiveFilters).toBe(true);
  });

  it('trims the search query before adding it to params and the hasActiveFilters check', () => {
    const { result } = renderHook(() => useLogFilters());
    act(() => {
      result.current.setSearchQuery('   ');
    });
    expect(result.current.queryParams).toEqual({ limit: 500 });
    expect(result.current.hasActiveFilters).toBe(false);

    act(() => {
      result.current.setSearchQuery('  needle  ');
    });
    expect(result.current.queryParams).toEqual({ search: 'needle', limit: 500 });
    expect(result.current.hasActiveFilters).toBe(true);
  });

  it('clearFilters resets every filter to its default', () => {
    const { result } = renderHook(() => useLogFilters());
    act(() => {
      result.current.setSelectedJobId('j');
      result.current.setSelectedHostId('h');
      result.current.setSelectedLevel('warn');
      result.current.setSelectedSince('1h');
      result.current.setSearchQuery('q');
    });
    expect(result.current.hasActiveFilters).toBe(true);

    act(() => {
      result.current.clearFilters();
    });
    expect(result.current.selectedJobId).toBe('all');
    expect(result.current.selectedHostId).toBe('all');
    expect(result.current.selectedLevel).toBe('all');
    expect(result.current.selectedSince).toBe('all');
    expect(result.current.searchQuery).toBe('');
    expect(result.current.hasActiveFilters).toBe(false);
  });
});
