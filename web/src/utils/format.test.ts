import { formatDate, formatSize, formatStorageSize, formatTimestamp, formatUptime, formatUptimeFromBoot } from './format';

describe('format utils', () => {
  it('formats sizes and storage', () => {
    expect(formatSize(1024 * 1024)).toBe('1 MB');
    expect(formatSize(2 * 1024 * 1024 * 1024)).toBe('2.0 GB');
    expect(formatStorageSize(1500)).toBe('1.5TB');
  });

  it('formats dates and timestamps', () => {
    expect(formatDate(null)).toBe('');
    expect(formatDate('2026-02-03T23:15:00+00:00Z')).toContain('2026');
    expect(formatTimestamp(null)).toBe('Never');
  });

  it('formats uptimes', () => {
    expect(formatUptime(0)).toBe('00:00:00');
    const value = formatUptimeFromBoot(new Date().toISOString());
    expect(value).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});
