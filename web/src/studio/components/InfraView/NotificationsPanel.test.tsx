import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';

// Mock the api module before importing the component
vi.mock('../../../api', () => ({
  getLabInfraNotifications: vi.fn(),
}));

import NotificationsPanel from './NotificationsPanel';
import { getLabInfraNotifications } from '../../../api';

const mockedGetNotifications = vi.mocked(getLabInfraNotifications);

// ─── Factories ─────────────────────────────────────────────────────

function makeNotification(overrides: Partial<any> = {}) {
  return {
    id: 'n1',
    severity: 'error' as const,
    category: 'tunnel_failed',
    title: 'Tunnel creation failed',
    detail: 'Connection timeout',
    entity_type: null,
    entity_name: null,
    timestamp: null,
    ...overrides,
  };
}

// ─── relativeTime replicated (not exported) ────────────────────────

function relativeTime(timestamp: string | null): string {
  if (!timestamp) return '';
  const diff = Date.now() - new Date(timestamp).getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

// ─── Tests ─────────────────────────────────────────────────────────

describe('NotificationsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state initially', () => {
    mockedGetNotifications.mockReturnValue(new Promise(() => {}));
    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    expect(screen.getByText('Loading notifications...')).toBeInTheDocument();
  });

  it('shows empty state when no notifications', async () => {
    mockedGetNotifications.mockResolvedValue({ notifications: [] });
    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    expect(await screen.findByText('No infrastructure issues detected')).toBeInTheDocument();
  });

  it('renders notification title', async () => {
    mockedGetNotifications.mockResolvedValue({
      notifications: [makeNotification({ title: 'VXLAN tunnel failed' })],
    });
    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    expect(await screen.findByText('VXLAN tunnel failed')).toBeInTheDocument();
  });

  it('renders category label mapping for tunnel_cleanup', async () => {
    mockedGetNotifications.mockResolvedValue({
      notifications: [makeNotification({ category: 'tunnel_cleanup' })],
    });
    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    expect(await screen.findByText('Tunnel Cleanup')).toBeInTheDocument();
  });

  it('renders category label mapping for link_error', async () => {
    mockedGetNotifications.mockResolvedValue({
      notifications: [makeNotification({ category: 'link_error' })],
    });
    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    expect(await screen.findByText('Link Error')).toBeInTheDocument();
  });

  it('renders raw category when no label mapping exists', async () => {
    mockedGetNotifications.mockResolvedValue({
      notifications: [makeNotification({ category: 'custom_category' })],
    });
    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    expect(await screen.findByText('custom_category')).toBeInTheDocument();
  });

  it('renders detail text when present', async () => {
    mockedGetNotifications.mockResolvedValue({
      notifications: [makeNotification({ detail: 'Agent unreachable on 10.0.0.2:8001' })],
    });
    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    expect(await screen.findByText('Agent unreachable on 10.0.0.2:8001')).toBeInTheDocument();
  });

  it('handles API error gracefully (shows empty state)', async () => {
    mockedGetNotifications.mockRejectedValue(new Error('Network error'));
    render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    expect(await screen.findByText('No infrastructure issues detected')).toBeInTheDocument();
  });

  it('re-fetches when refreshKey changes', async () => {
    mockedGetNotifications.mockResolvedValue({ notifications: [] });
    const { rerender } = render(<NotificationsPanel labId="lab-1" refreshKey={0} />);
    await screen.findByText('No infrastructure issues detected');
    expect(mockedGetNotifications).toHaveBeenCalledTimes(1);

    mockedGetNotifications.mockResolvedValue({ notifications: [makeNotification()] });
    rerender(<NotificationsPanel labId="lab-1" refreshKey={1} />);
    expect(await screen.findByText('Tunnel creation failed')).toBeInTheDocument();
    expect(mockedGetNotifications).toHaveBeenCalledTimes(2);
  });

  it('calls getLabInfraNotifications with labId', async () => {
    mockedGetNotifications.mockResolvedValue({ notifications: [] });
    render(<NotificationsPanel labId="my-lab-42" refreshKey={0} />);
    await screen.findByText('No infrastructure issues detected');
    expect(mockedGetNotifications).toHaveBeenCalledWith('my-lab-42');
  });
});

// ─── relativeTime pure function tests ──────────────────────────────

describe('relativeTime', () => {
  it('returns empty string for null', () => {
    expect(relativeTime(null)).toBe('');
  });

  it('returns "just now" for timestamps < 60s ago', () => {
    const ts = new Date(Date.now() - 30_000).toISOString();
    expect(relativeTime(ts)).toBe('just now');
  });

  it('returns minutes for timestamps < 1h ago', () => {
    const ts = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(relativeTime(ts)).toBe('5m ago');
  });

  it('returns hours for timestamps < 24h ago', () => {
    const ts = new Date(Date.now() - 3 * 3_600_000).toISOString();
    expect(relativeTime(ts)).toBe('3h ago');
  });

  it('returns days for timestamps >= 24h ago', () => {
    const ts = new Date(Date.now() - 2 * 86_400_000).toISOString();
    expect(relativeTime(ts)).toBe('2d ago');
  });
});
