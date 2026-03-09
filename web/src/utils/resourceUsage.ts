export interface MemoryUsageLike {
  memory_percent: number;
  memory_used_gb: number;
  memory_total_gb: number;
}

export interface MemoryUsageDisplay {
  percent: number;
  usedGb: number;
  totalGb: number;
  hasTotals: boolean;
}

export function getMemoryUsageDisplay(usage: MemoryUsageLike): MemoryUsageDisplay {
  const usedGb = Number.isFinite(usage.memory_used_gb) ? usage.memory_used_gb : 0;
  const totalGb = Number.isFinite(usage.memory_total_gb) ? usage.memory_total_gb : 0;
  const reportedPercent = Number.isFinite(usage.memory_percent) ? usage.memory_percent : 0;

  if (totalGb > 0) {
    const boundedUsed = Math.max(0, Math.min(usedGb, totalGb));
    return {
      percent: Math.max(0, Math.min((boundedUsed / totalGb) * 100, 100)),
      usedGb: boundedUsed,
      totalGb,
      hasTotals: true,
    };
  }

  return {
    percent: Math.max(0, Math.min(reportedPercent, 100)),
    usedGb,
    totalGb,
    hasTotals: false,
  };
}
