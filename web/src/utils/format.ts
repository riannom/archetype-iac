/**
 * Unified formatting utilities for the Archetype frontend.
 * Consolidates duplicated formatting functions across components.
 */

/**
 * Format bytes into human-readable size (MB/GB).
 * @param bytes - Size in bytes (can be null/undefined)
 * @returns Formatted string like "1.5 GB" or "256 MB", empty string if null
 */
export function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return '';
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(0)} MB`;
}

/**
 * Format gigabytes into human-readable storage size (GB/TB).
 * @param gb - Size in gigabytes
 * @returns Formatted string like "1.5TB" or "256.0GB"
 */
export function formatStorageSize(gb: number): string {
  if (gb >= 1000) {
    return `${(gb / 1000).toFixed(1)}TB`;
  }
  return `${gb.toFixed(1)}GB`;
}

/**
 * Format a date string into localized date format.
 * @param dateStr - ISO date string (can be null)
 * @returns Formatted date like "Jan 15, 2024", empty string if null
 */
export function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * Format a timestamp into relative time (e.g., "5m ago").
 * Falls back to localized date for older timestamps.
 * @param ts - ISO timestamp string (can be null)
 * @returns Relative time like "5s ago", "10m ago", "2h ago", or date if > 24h
 */
export function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return 'Never';
  const date = new Date(ts);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return date.toLocaleDateString();
}

/**
 * Format milliseconds into uptime display (HH:MM:SS).
 * @param ms - Duration in milliseconds
 * @returns Formatted uptime like "01:23:45"
 */
export function formatUptime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
}

/**
 * Format a boot timestamp into live uptime.
 * Returns null if no boot timestamp provided.
 * @param bootStartedAt - ISO timestamp when boot started
 * @returns Formatted uptime or null
 */
export function formatUptimeFromBoot(bootStartedAt: string | null | undefined): string | null {
  if (!bootStartedAt) return null;
  const bootTime = new Date(bootStartedAt).getTime();
  const elapsed = Date.now() - bootTime;
  return formatUptime(elapsed);
}
