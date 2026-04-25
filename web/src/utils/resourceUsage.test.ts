import { describe, it, expect } from 'vitest';
import { getMemoryUsageDisplay } from './resourceUsage';

describe('getMemoryUsageDisplay', () => {
  it('computes percent from totals when totalGb > 0', () => {
    const result = getMemoryUsageDisplay({
      memory_percent: 0,
      memory_used_gb: 4,
      memory_total_gb: 16,
    });
    expect(result).toEqual({ percent: 25, usedGb: 4, totalGb: 16, hasTotals: true });
  });

  it('clamps usedGb to [0, totalGb] when totals are present', () => {
    expect(getMemoryUsageDisplay({
      memory_percent: 0,
      memory_used_gb: 999,
      memory_total_gb: 8,
    })).toEqual({ percent: 100, usedGb: 8, totalGb: 8, hasTotals: true });

    expect(getMemoryUsageDisplay({
      memory_percent: 0,
      memory_used_gb: -5,
      memory_total_gb: 8,
    })).toEqual({ percent: 0, usedGb: 0, totalGb: 8, hasTotals: true });
  });

  it('falls back to reported percent when totalGb is 0', () => {
    expect(getMemoryUsageDisplay({
      memory_percent: 73,
      memory_used_gb: 0,
      memory_total_gb: 0,
    })).toEqual({ percent: 73, usedGb: 0, totalGb: 0, hasTotals: false });
  });

  it('clamps reported percent to [0, 100] when totalGb is 0', () => {
    expect(getMemoryUsageDisplay({
      memory_percent: 250,
      memory_used_gb: 0,
      memory_total_gb: 0,
    }).percent).toBe(100);

    expect(getMemoryUsageDisplay({
      memory_percent: -10,
      memory_used_gb: 0,
      memory_total_gb: 0,
    }).percent).toBe(0);
  });

  it('treats non-finite numbers as 0', () => {
    const result = getMemoryUsageDisplay({
      memory_percent: NaN,
      memory_used_gb: Infinity,
      memory_total_gb: -Infinity,
    });
    // totalGb falls back to 0, so we go through the no-totals branch with reportedPercent=0
    expect(result).toEqual({ percent: 0, usedGb: 0, totalGb: 0, hasTotals: false });
  });

  it('uses reported percent when totalGb is non-finite (treated as 0)', () => {
    const result = getMemoryUsageDisplay({
      memory_percent: 42,
      memory_used_gb: NaN,
      memory_total_gb: NaN,
    });
    expect(result).toEqual({ percent: 42, usedGb: 0, totalGb: 0, hasTotals: false });
  });
});
