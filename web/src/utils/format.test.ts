import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  formatDate,
  formatMemorySize,
  formatSize,
  formatStorageSize,
  formatTimestamp,
  formatUptime,
  formatUptimeFromBoot,
} from './format';

afterEach(() => {
  vi.useRealTimers();
});

describe('formatSize', () => {
  it('returns empty string for null/undefined/zero', () => {
    expect(formatSize(null)).toBe('');
    expect(formatSize(undefined)).toBe('');
    expect(formatSize(0)).toBe('');
  });

  it('formats sub-1GB sizes in MB (rounded to whole number)', () => {
    expect(formatSize(1024 * 1024)).toBe('1 MB');
    expect(formatSize(512 * 1024 * 1024)).toBe('512 MB');
  });

  it('formats >=1GB sizes in GB (one decimal)', () => {
    expect(formatSize(2 * 1024 * 1024 * 1024)).toBe('2.0 GB');
    expect(formatSize(1.5 * 1024 * 1024 * 1024)).toBe('1.5 GB');
  });
});

describe('formatStorageSize', () => {
  it('formats sub-1000 GB as GB', () => {
    expect(formatStorageSize(256)).toBe('256.0GB');
    expect(formatStorageSize(0)).toBe('0.0GB');
    expect(formatStorageSize(999.9)).toBe('999.9GB');
  });

  it('formats >=1000 GB as TB', () => {
    expect(formatStorageSize(1500)).toBe('1.5TB');
    expect(formatStorageSize(1000)).toBe('1.0TB');
  });
});

describe('formatDate', () => {
  it('returns empty string for null/undefined/empty', () => {
    expect(formatDate(null)).toBe('');
    expect(formatDate(undefined)).toBe('');
    expect(formatDate('')).toBe('');
  });

  it('returns empty string for invalid date', () => {
    expect(formatDate('not-a-date')).toBe('');
  });

  it('formats valid ISO dates', () => {
    expect(formatDate('2026-02-03T00:00:00Z')).toContain('2026');
  });

  it('cleans up malformed `+00:00Z` suffix', () => {
    expect(formatDate('2026-02-03T23:15:00+00:00Z')).toContain('2026');
  });
});

describe('formatTimestamp', () => {
  it('returns "Never" for null/undefined', () => {
    expect(formatTimestamp(null)).toBe('Never');
    expect(formatTimestamp(undefined)).toBe('Never');
  });

  it('formats sub-minute relative times', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-25T12:00:30Z'));
    expect(formatTimestamp('2026-04-25T12:00:00Z')).toBe('30s ago');
  });

  it('formats sub-hour relative times', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-25T12:30:00Z'));
    expect(formatTimestamp('2026-04-25T12:00:00Z')).toBe('30m ago');
  });

  it('formats sub-day relative times', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-25T20:00:00Z'));
    expect(formatTimestamp('2026-04-25T12:00:00Z')).toBe('8h ago');
  });

  it('falls back to localeDateString for >24h ago', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-25T12:00:00Z'));
    const result = formatTimestamp('2026-04-22T12:00:00Z');
    // Locale date string varies, but should NOT contain "ago"
    expect(result).not.toMatch(/ago$/);
    expect(result.length).toBeGreaterThan(0);
  });
});

describe('formatUptime', () => {
  it('formats zero', () => {
    expect(formatUptime(0)).toBe('00:00:00');
  });

  it('formats sub-second to one second', () => {
    expect(formatUptime(500)).toBe('00:00:00');
    expect(formatUptime(1000)).toBe('00:00:01');
  });

  it('formats minutes and seconds', () => {
    expect(formatUptime((2 * 60 + 5) * 1000)).toBe('00:02:05');
  });

  it('formats hours/minutes/seconds with zero-padding', () => {
    const ms = (3 * 3600 + 7 * 60 + 9) * 1000;
    expect(formatUptime(ms)).toBe('03:07:09');
  });
});

describe('formatUptimeFromBoot', () => {
  it('returns null for null/undefined boot timestamp', () => {
    expect(formatUptimeFromBoot(null)).toBeNull();
    expect(formatUptimeFromBoot(undefined)).toBeNull();
    expect(formatUptimeFromBoot('')).toBeNull();
  });

  it('returns formatted uptime when given a boot timestamp', () => {
    const value = formatUptimeFromBoot(new Date().toISOString());
    expect(value).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

describe('formatMemorySize', () => {
  it('formats sub-1GB amounts in MB', () => {
    expect(formatMemorySize(0.5)).toBe('512 MB');
    expect(formatMemorySize(0.25)).toBe('256 MB');
  });

  it('formats 1-1023 GB amounts in GB', () => {
    expect(formatMemorySize(1)).toBe('1.0 GB');
    expect(formatMemorySize(8.5)).toBe('8.5 GB');
    expect(formatMemorySize(1023)).toBe('1023.0 GB');
  });

  it('formats >=1024 GB amounts in TB', () => {
    expect(formatMemorySize(1024)).toBe('1.0 TB');
    expect(formatMemorySize(2048)).toBe('2.0 TB');
  });
});
