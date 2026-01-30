import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  formatSize,
  formatStorageSize,
  formatDate,
  formatTimestamp,
  formatUptime,
  formatUptimeFromBoot,
} from "./format";

describe("formatSize", () => {
  it("returns empty string for null/undefined", () => {
    expect(formatSize(null)).toBe("");
    expect(formatSize(undefined)).toBe("");
    expect(formatSize(0)).toBe("");
  });

  it("formats bytes into MB for small sizes", () => {
    const megabyte = 1024 * 1024;
    expect(formatSize(megabyte)).toBe("1 MB");
    expect(formatSize(256 * megabyte)).toBe("256 MB");
  });

  it("formats bytes into GB for large sizes", () => {
    const gigabyte = 1024 * 1024 * 1024;
    expect(formatSize(gigabyte)).toBe("1.0 GB");
    expect(formatSize(1.5 * gigabyte)).toBe("1.5 GB");
    expect(formatSize(10 * gigabyte)).toBe("10.0 GB");
  });
});

describe("formatStorageSize", () => {
  it("formats gigabytes with one decimal", () => {
    expect(formatStorageSize(100)).toBe("100.0GB");
    expect(formatStorageSize(256.5)).toBe("256.5GB");
  });

  it("converts to TB for values >= 1000 GB", () => {
    expect(formatStorageSize(1000)).toBe("1.0TB");
    expect(formatStorageSize(1500)).toBe("1.5TB");
    expect(formatStorageSize(2048)).toBe("2.0TB");
  });
});

describe("formatDate", () => {
  it("returns empty string for null/undefined", () => {
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
  });

  it("formats ISO date string", () => {
    const result = formatDate("2024-01-15T10:30:00Z");
    // Format depends on locale but should contain the date parts
    expect(result).toContain("15");
    expect(result).toContain("2024");
    expect(result).toMatch(/Jan/i);
  });
});

describe("formatTimestamp", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-01-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns 'Never' for null/undefined", () => {
    expect(formatTimestamp(null)).toBe("Never");
    expect(formatTimestamp(undefined)).toBe("Never");
  });

  it("formats seconds ago", () => {
    const thirtySecondsAgo = new Date(Date.now() - 30 * 1000).toISOString();
    expect(formatTimestamp(thirtySecondsAgo)).toBe("30s ago");
  });

  it("formats minutes ago", () => {
    const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    expect(formatTimestamp(fiveMinutesAgo)).toBe("5m ago");
  });

  it("formats hours ago", () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    expect(formatTimestamp(twoHoursAgo)).toBe("2h ago");
  });

  it("shows date for > 24 hours", () => {
    const twoDaysAgo = new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString();
    const result = formatTimestamp(twoDaysAgo);
    // Should show a date format, not relative time
    expect(result).not.toContain("ago");
  });
});

describe("formatUptime", () => {
  it("formats milliseconds into HH:MM:SS", () => {
    // 0 ms
    expect(formatUptime(0)).toBe("00:00:00");

    // 1 minute 30 seconds
    expect(formatUptime(90000)).toBe("00:01:30");

    // 1 hour 23 minutes 45 seconds
    expect(formatUptime(5025000)).toBe("01:23:45");

    // 25 hours
    expect(formatUptime(90000000)).toBe("25:00:00");
  });

  it("pads single digits with zeros", () => {
    // 5 seconds
    expect(formatUptime(5000)).toBe("00:00:05");

    // 5 minutes 5 seconds
    expect(formatUptime(305000)).toBe("00:05:05");
  });
});

describe("formatUptimeFromBoot", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-01-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns null for null/undefined boot time", () => {
    expect(formatUptimeFromBoot(null)).toBeNull();
    expect(formatUptimeFromBoot(undefined)).toBeNull();
  });

  it("calculates uptime from boot timestamp", () => {
    // Boot started 1 hour 30 minutes ago
    const bootTime = new Date(Date.now() - 90 * 60 * 1000).toISOString();
    expect(formatUptimeFromBoot(bootTime)).toBe("01:30:00");
  });

  it("handles boot time equal to current time", () => {
    const bootTime = new Date().toISOString();
    expect(formatUptimeFromBoot(bootTime)).toBe("00:00:00");
  });
});
