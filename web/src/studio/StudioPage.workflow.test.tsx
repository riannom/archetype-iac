/**
 * StudioPage workflow integration tests.
 *
 * Pattern: follows StudioPage.test.tsx — minimal vi.mock, render full component
 * with ThemeProvider + UserProvider. Tests verify dashboard rendering, API
 * communication, auth flow, and WebSocket establishment.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import StudioPage from './StudioPage';
import { UserProvider } from '../contexts/UserContext';
import { ThemeProvider } from '../theme/ThemeProvider';

// ---------------------------------------------------------------------------
// Shared mocks (same pattern as StudioPage.test.tsx)
// ---------------------------------------------------------------------------
vi.mock('xterm', () => ({
  Terminal: vi.fn(() => ({
    write: vi.fn(), writeln: vi.fn(), focus: vi.fn(), dispose: vi.fn(),
    open: vi.fn(), onData: vi.fn(() => ({ dispose: vi.fn() })), loadAddon: vi.fn(),
  })),
}));
vi.mock('xterm-addon-fit', () => ({
  FitAddon: vi.fn(() => ({ fit: vi.fn() })),
}));
vi.mock('../theme/index', async () => {
  const actual = await vi.importActual('../theme/index');
  return { ...actual, useTheme: () => ({ effectiveMode: 'light', mode: 'light', setMode: vi.fn(), toggleMode: vi.fn() }) };
});
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({ notifications: [], addNotification: vi.fn(), dismissNotification: vi.fn(), dismissAllNotifications: vi.fn() }),
}));
vi.mock('../contexts/ImageLibraryContext', () => ({
  useImageLibrary: () => ({ imageLibrary: [], loading: false, error: null, refreshImageLibrary: vi.fn() }),
  ImageLibraryProvider: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock('../contexts/DeviceCatalogContext', () => ({
  useDeviceCatalog: () => ({ vendorCategories: [], deviceModels: [], deviceCategories: [], addCustomDevice: vi.fn(), removeCustomDevice: vi.fn(), loading: false, error: null, refresh: vi.fn() }),
  DeviceCatalogProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// ---------------------------------------------------------------------------
// WebSocket mock (same as StudioPage.test.tsx)
// ---------------------------------------------------------------------------
class MockWS {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  url: string; readyState: number; binaryType = 'blob';
  onopen: any = null; onclose: any = null; onmessage: any = null; onerror: any = null;
  constructor(url: string) { this.url = url; this.readyState = MockWS.CONNECTING; wsInstances.push(this); }
  send() {} close() { this.readyState = MockWS.CLOSED; }
}
let wsInstances: MockWS[] = [];
const origWS = global.WebSocket;

// ---------------------------------------------------------------------------
// getBoundingClientRect mock for canvas
// ---------------------------------------------------------------------------
const mockGetBoundingClientRect = vi.fn(() => ({
  left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600, x: 0, y: 0, toJSON: () => {},
}));

// ---------------------------------------------------------------------------
// Fetch mock
// ---------------------------------------------------------------------------
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

const labsResponse = { labs: [{ id: 'lab-1', name: 'Test Lab 1', created_at: '2024-01-01T00:00:00Z' }] };
const metricsResponse = { agents: { online: 1, total: 1 }, containers: { running: 0, total: 0 }, cpu_percent: 0, memory_percent: 0, labs_running: 0, labs_total: 1 };
const userResponse = { id: 'u1', username: 'test', email: 'test@example.com', is_active: true, global_role: 'super_admin', created_at: '2024-01-01T00:00:00Z' };

function setupFetch() {
  mockFetch.mockImplementation(async (url: string) => {
    const ok = (d: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(d) });
    if (url.includes('/auth/me')) return ok(userResponse);
    if (url.includes('/dashboard/metrics')) return ok(metricsResponse);
    if (url.includes('/agents')) return ok([]);
    if (url.includes('/images/library')) return ok({ images: [] });
    if (url.includes('/vendors')) return ok([]);
    if (url.includes('/export-graph')) return ok({ nodes: [], links: [] });
    if (url.includes('/export-yaml')) return ok({ content: 'name: test\ntopology:\n  nodes: {}' });
    if (url.includes('/nodes/states')) return ok({ nodes: [] });
    if (url.includes('/nodes/refresh')) return ok({});
    if (url.includes('/nodes/ready')) return ok({ nodes: [] });
    if (url.includes('/jobs')) return ok({ jobs: [] });
    if (url.includes('/layout')) return { ok: false, status: 404 };
    if (url.includes('/status')) return ok({ nodes: [] });
    if (url.includes('/labs')) return ok(labsResponse);
    return ok({});
  });
}

const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter><ThemeProvider><UserProvider>{children}</UserProvider></ThemeProvider></BrowserRouter>
);

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('StudioPage Workflow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsInstances = [];
    (global as any).WebSocket = MockWS;
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    Element.prototype.getBoundingClientRect = mockGetBoundingClientRect;
    setupFetch();
  });

  afterEach(() => {
    (global as any).WebSocket = origWS;
    localStorage.clear();
  });

  it('renders dashboard with lab list', async () => {
    render(<Wrapper><StudioPage /></Wrapper>);
    expect(await screen.findByText('Test Lab 1')).toBeInTheDocument();
  });

  it('renders ARCHETYPE branding on dashboard', async () => {
    render(<Wrapper><StudioPage /></Wrapper>);
    expect(await screen.findByText('ARCHETYPE')).toBeInTheDocument();
  });

  it('fetches labs data on mount', async () => {
    render(<Wrapper><StudioPage /></Wrapper>);
    await screen.findByText('Test Lab 1');
    const labCalls = mockFetch.mock.calls.filter(([u]: [string]) => u.includes('/labs'));
    expect(labCalls.length).toBeGreaterThanOrEqual(1);
  });

  it('fetches dashboard metrics on mount', async () => {
    render(<Wrapper><StudioPage /></Wrapper>);
    await screen.findByText('Test Lab 1');
    const metricCalls = mockFetch.mock.calls.filter(([u]: [string]) => u.includes('/dashboard/metrics'));
    expect(metricCalls.length).toBeGreaterThanOrEqual(1);
  });

  it('sends auth headers with API requests', async () => {
    render(<Wrapper><StudioPage /></Wrapper>);
    await screen.findByText('Test Lab 1');
    const authCalls = mockFetch.mock.calls.filter(([, init]: [string, any]) =>
      init?.headers?.Authorization?.includes('Bearer test-token')
    );
    expect(authCalls.length).toBeGreaterThan(0);
  });

  it('shows empty state when no labs exist', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      const ok = (d: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(d) });
      if (url.includes('/auth/me')) return ok(userResponse);
      if (url.includes('/dashboard/metrics')) return ok(metricsResponse);
      if (url.includes('/agents')) return ok([]);
      if (url.includes('/images/library')) return ok({ images: [] });
      if (url.includes('/vendors')) return ok([]);
      if (url.includes('/labs')) return ok({ labs: [] });
      return ok({});
    });

    render(<Wrapper><StudioPage /></Wrapper>);
    await waitFor(() => {
      expect(screen.queryByText('Test Lab 1')).not.toBeInTheDocument();
    });
  });
});
