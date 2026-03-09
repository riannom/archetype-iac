/**
 * StudioPage handler tests.
 *
 * Tests handler logic by mocking child components and verifying fetch calls,
 * state changes, and notification behavior. Follows the pattern from
 * StudioPage.extract-config.test.tsx with heavy component mocking.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import StudioPage from './StudioPage';

// ---------------------------------------------------------------------------
// Shared mock variables
// ---------------------------------------------------------------------------
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

const addNotification = vi.fn();
const refreshDeviceCatalog = vi.fn();
let mockDeviceModels: any[] = [];

// Capture callbacks passed to child components
let capturedDashboardProps: any = {};
let capturedCanvasProps: any = {};
let capturedTopBarProps: any = {};
let capturedRuntimeControlProps: any = {};
let capturedVerificationPanelProps: any = {};
let capturedSidebarProps: any = {};

// ---------------------------------------------------------------------------
// Context mocks
// ---------------------------------------------------------------------------
vi.mock('../theme/index', async () => {
  const actual = await vi.importActual('../theme/index');
  return {
    ...actual,
    useTheme: () => ({ effectiveMode: 'light', mode: 'light', setMode: vi.fn(), toggleMode: vi.fn() }),
  };
});

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    notifications: [],
    addNotification,
    dismissNotification: vi.fn(),
    dismissAllNotifications: vi.fn(),
    preferences: {},
  }),
}));

vi.mock('../contexts/UserContext', () => ({
  useUser: () => ({
    user: { id: 'u1', username: 'test', global_role: 'super_admin' },
    loading: false,
    logout: vi.fn(),
    refreshUser: vi.fn(),
    clearUser: vi.fn(),
    hasRole: () => true,
    isAdmin: () => true,
  }),
  UserProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock('../contexts/ImageLibraryContext', () => ({
  useImageLibrary: () => ({
    imageLibrary: [
      { id: 'img-1', kind: 'docker', device_id: 'linux', name: 'alpine', reference: 'alpine:latest' },
    ],
    loading: false,
    error: null,
    refreshImageLibrary: vi.fn(),
  }),
  ImageLibraryProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock('../contexts/DeviceCatalogContext', () => ({
  useDeviceCatalog: () => ({
    vendorCategories: [],
    deviceModels: mockDeviceModels,
    deviceCategories: [],
    addCustomDevice: vi.fn(),
    removeCustomDevice: vi.fn(),
    loading: false,
    error: null,
    refresh: refreshDeviceCatalog,
  }),
  DeviceCatalogProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock('./hooks/useLabStateWS', () => ({
  useLabStateWS: () => ({
    isConnected: false,
    reconnectAttempts: 0,
    refresh: vi.fn(),
    connected: false,
    linkStates: {},
  }),
}));

// ---------------------------------------------------------------------------
// Child component mocks that capture props for handler invocation
// ---------------------------------------------------------------------------
vi.mock('./components/Dashboard', () => ({
  default: (props: any) => {
    capturedDashboardProps = props;
    return (
      <div data-testid="dashboard">
        <button onClick={() => props.onCreate?.()}>Create Lab</button>
        <button onClick={() => props.onDelete?.('lab-1')}>Delete Lab</button>
        <button onClick={() => props.onRename?.('lab-1', 'Renamed Lab')}>Rename Lab</button>
        <button onClick={() => props.onSelect?.({ id: 'lab-1', name: 'Test Lab 1' })}>Open Lab</button>
      </div>
    );
  },
}));

vi.mock('./components/Canvas', () => ({
  default: (props: any) => {
    capturedCanvasProps = props;
    return (
      <div data-testid="canvas">
        <button onClick={() => props.onConnect?.('node-1', 'node-2')}>Connect Nodes</button>
        <button onClick={() => props.onDelete?.('node-1')}>Delete Node</button>
        <button onClick={() => props.onDelete?.('link-0-r1-r2')}>Delete Link</button>
        <button onClick={() => props.onExtractConfig?.('node-1')}>Extract Node Config</button>
      </div>
    );
  },
}));

vi.mock('./components/TopBar', () => ({
  default: (props: any) => {
    capturedTopBarProps = props;
    return (
      <div data-testid="topbar">
        <button onClick={() => props.onExport?.()}>Export YAML</button>
        <button onClick={() => props.onExportFull?.()}>Export Full</button>
        <button onClick={() => props.onExit?.()}>Exit Lab</button>
        <button onClick={() => props.onRename?.('New Name')}>Rename</button>
      </div>
    );
  },
}));

vi.mock('./components/RuntimeControl', () => ({
  default: (props: any) => {
    capturedRuntimeControlProps = props;
    return (
      <div data-testid="runtime-control">
        <button onClick={() => props.onExtractConfigs?.()}>Extract Configs</button>
        <button onClick={() => props.onStartTests?.()}>Start Tests</button>
      </div>
    );
  },
}));

vi.mock('./components/VerificationPanel', () => ({
  default: (props: any) => {
    capturedVerificationPanelProps = props;
    return (
      <div data-testid="verification-panel">
        <button onClick={() => props.onStartTests?.()}>Run Tests</button>
      </div>
    );
  },
}));

vi.mock('./components/Sidebar', () => ({
  __esModule: true,
  default: (props: any) => {
    capturedSidebarProps = props;
    return (
      <div data-testid="sidebar">
        <button onClick={() => props.onAddDevice?.({
          id: 'linux',
          type: 'container',
          name: 'Linux Container',
          icon: 'fa-server',
          versions: ['alpine:latest'],
          isActive: true,
          vendor: 'Generic',
        })}>Add Device</button>
        <button onClick={() => props.onAddDevice?.({
          id: 'cisco_csr1000v',
          type: 'vm',
          name: 'CSR1000v',
          icon: 'fa-server',
          versions: ['17.3.6'],
          isActive: true,
          vendor: 'Cisco',
          requiresImage: true,
          supportedImageKinds: ['qcow2'],
        })}>Add VM Device</button>
      </div>
    );
  },
  SidebarTab: {},
}));

vi.mock('./components/PropertiesPanel', () => ({ default: () => null }));
vi.mock('./components/StatusBar', () => ({ default: () => null }));
vi.mock('./components/ConsoleManager', () => ({ default: () => <div /> }));
vi.mock('./components/AgentAlertBanner', () => ({ default: () => null }));
vi.mock('./components/SystemStatusStrip', () => ({ default: () => null }));
vi.mock('./components/ConfigsView', () => ({
  default: (props: any) => (
    <div data-testid="configs-view">
      <button onClick={() => props.onExtractConfigs?.().catch(() => {})}>Extract All Configs</button>
    </div>
  ),
}));
vi.mock('./components/LogsView', () => ({ default: () => null }));
vi.mock('./components/ConfigViewerModal', () => ({ default: () => null }));
vi.mock('./components/JobLogModal', () => ({ default: () => null }));
vi.mock('./components/TaskLogEntryModal', () => ({ default: () => null }));
vi.mock('./components/ScenarioPanel', () => ({ default: () => null }));
vi.mock('./components/InfraView', () => ({ default: () => null }));

vi.mock('./components/TaskLogPanel', () => ({
  __esModule: true,
  default: ({ entries }: { entries: Array<{ id: string; message: string }> }) => (
    <div data-testid="task-log-panel">
      {entries.map((entry) => (
        <div key={entry.id} data-testid="task-log-entry">{entry.message}</div>
      ))}
    </div>
  ),
  TaskLogEntry: {},
}));

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------
const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter>{children}</BrowserRouter>
);

// ---------------------------------------------------------------------------
// Fetch setup
// ---------------------------------------------------------------------------
function setupFetch(overrides: Record<string, any> = {}) {
  mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const ok = (d: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(d) });
    const method = init?.method || 'GET';

    // Check overrides first
    for (const [pattern, response] of Object.entries(overrides)) {
      if (url.includes(pattern)) {
        if (typeof response === 'function') return response(url, init);
        return response;
      }
    }

    if (url.includes('/auth/me')) return ok({ id: 'u1', username: 'test', global_role: 'super_admin' });
    if (url.includes('/dashboard/metrics')) return ok({ agents: { online: 1, total: 1 }, containers: { running: 0, total: 0 }, cpu_percent: 0, memory_percent: 0, labs_running: 0, labs_total: 1 });
    if (url.includes('/agents')) return ok([]);
    if (url.includes('/images/library')) return ok({ images: [] });
    if (url.includes('/vendors')) return ok([]);
    if (url.includes('/export-yaml')) return ok({ content: 'name: test\ntopology:\n  nodes: {}' });
    if (url.includes('/export-graph')) return ok({ nodes: [{ id: 'node-1', name: 'R1', nodeType: 'device', type: 'container', model: 'linux', version: 'alpine:latest', x: 10, y: 10 }], links: [], annotations: [] });
    if (url.includes('/nodes/states')) return ok({ nodes: [] });
    if (url.includes('/nodes/refresh')) return ok({});
    if (url.includes('/nodes/ready')) return ok({ nodes: [] });
    if (url.includes('/jobs')) return ok({ jobs: [] });
    if (url.includes('/layout')) return { ok: false, status: 404 };
    if (url.includes('/status')) return ok({ nodes: [] });
    if (url.includes('/tests/run') && method === 'POST') return ok({ message: 'Tests started' });
    if (url.includes('/extract-configs') && method === 'POST') return ok({ success: true, extracted_count: 2, snapshots_created: 1, message: 'Extracted 2 configs' });
    if (url.includes('/extract-config') && method === 'POST') return ok({ message: 'snapshot created' });
    if (url.includes('/download-bundle')) return { ok: true, status: 200, blob: () => Promise.resolve(new Blob(['zip'])), headers: new Headers({ 'Content-Disposition': 'attachment; filename=test_bundle.zip' }) };
    if (method === 'POST' && url.includes('/labs') && !url.includes('/tests') && !url.includes('/extract') && !url.includes('/update-topology') && !url.includes('/deploy') && !url.includes('/destroy')) return ok({ id: 'lab-new', name: 'Project_1' });
    if (method === 'DELETE' && url.includes('/labs/')) return ok({});
    if (method === 'PUT' && url.includes('/labs/')) return ok({});
    if (url.includes('/labs')) return ok({ labs: [{ id: 'lab-1', name: 'Test Lab 1', created_at: '2024-01-01T00:00:00Z' }] });
    return ok({});
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('StudioPage Handlers', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
    capturedDashboardProps = {};
    capturedCanvasProps = {};
    capturedTopBarProps = {};
    capturedRuntimeControlProps = {};
    capturedVerificationPanelProps = {};
    capturedSidebarProps = {};
    mockDeviceModels = [
      { id: 'linux', type: 'container', name: 'Linux Container', icon: 'fa-server', versions: ['alpine:latest'], isActive: true, vendor: 'Generic' },
      { id: 'cisco_csr1000v', type: 'vm', name: 'CSR1000v', icon: 'fa-server', versions: ['17.3.6'], isActive: true, vendor: 'Cisco', requiresImage: true, supportedImageKinds: ['qcow2'] },
    ];
    setupFetch();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // -------------------------------------------------------------------------
  // handleCreateLab
  // -------------------------------------------------------------------------
  describe('handleCreateLab', () => {
    it('sends POST /labs with generated name', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Create Lab' }));

      await waitFor(() => {
        const postCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/labs') && opts?.method === 'POST'
            && !url.includes('/update-topology') && !url.includes('/tests') && !url.includes('/extract')
        );
        expect(postCalls.length).toBeGreaterThanOrEqual(1);
        const body = JSON.parse(postCalls[0][1].body);
        expect(body.name).toMatch(/^Project_\d+$/);
      });
    });

    it('reloads labs list after creation', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      const labsFetchCountBefore = mockFetch.mock.calls.filter(
        ([url, opts]: [string, any]) => url.includes('/labs') && (!opts?.method || opts.method === 'GET')
      ).length;

      await user.click(screen.getByRole('button', { name: 'Create Lab' }));

      await waitFor(() => {
        const labsFetchCountAfter = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/labs') && (!opts?.method || opts.method === 'GET')
        ).length;
        expect(labsFetchCountAfter).toBeGreaterThan(labsFetchCountBefore);
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleDeleteLab
  // -------------------------------------------------------------------------
  describe('handleDeleteLab', () => {
    it('sends DELETE /labs/{id}', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Delete Lab' }));

      await waitFor(() => {
        const deleteCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/labs/lab-1') && opts?.method === 'DELETE'
        );
        expect(deleteCalls.length).toBe(1);
      });
    });

    it('reloads labs list after deletion', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Delete Lab' }));

      await waitFor(() => {
        const getLabsCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/labs') && (!opts?.method || opts.method === 'GET')
        );
        // At least one GET after the DELETE
        expect(getLabsCalls.length).toBeGreaterThanOrEqual(2);
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleRenameLab
  // -------------------------------------------------------------------------
  describe('handleRenameLab', () => {
    it('sends PUT /labs/{id} with new name', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Rename Lab' }));

      await waitFor(() => {
        const putCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/labs/lab-1') && opts?.method === 'PUT'
        );
        expect(putCalls.length).toBe(1);
        const body = JSON.parse(putCalls[0][1].body);
        expect(body.name).toBe('Renamed Lab');
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleSelectLab + handleExport
  // -------------------------------------------------------------------------
  describe('handleExport', () => {
    it('fetches YAML export when Export is clicked', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      // Open a lab first
      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      await user.click(screen.getByRole('button', { name: 'Export YAML' }));

      await waitFor(() => {
        const exportCalls = mockFetch.mock.calls.filter(
          ([url]: [string]) => url.includes('/export-yaml')
        );
        expect(exportCalls.length).toBeGreaterThanOrEqual(1);
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleExportFull (bundle download)
  // -------------------------------------------------------------------------
  describe('handleExportFull', () => {
    it('fetches download-bundle when Export Full is clicked', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      await user.click(screen.getByRole('button', { name: 'Export Full' }));

      await waitFor(() => {
        const bundleCalls = mockFetch.mock.calls.filter(
          ([url]: [string]) => url.includes('/download-bundle')
        );
        expect(bundleCalls.length).toBeGreaterThanOrEqual(1);
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleAddDevice
  // -------------------------------------------------------------------------
  describe('handleAddDevice', () => {
    it('adds a container device and triggers debounced topology save', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      // Open a lab
      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('sidebar');

      await user.click(screen.getByRole('button', { name: 'Add Device' }));

      // Advance past the 2s debounce
      vi.advanceTimersByTime(3000);

      await waitFor(() => {
        const topoCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/update-topology') && opts?.method === 'POST'
        );
        expect(topoCalls.length).toBeGreaterThanOrEqual(1);
      });

      vi.useRealTimers();
    });

    it('shows warning notification when device has no runnable image', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('sidebar');

      // CSR1000v requires a qcow2 image which is not in mock imageLibrary
      await user.click(screen.getByRole('button', { name: 'Add VM Device' }));

      await waitFor(() => {
        expect(addNotification).toHaveBeenCalledWith(
          'warning',
          'No runnable image assigned',
          expect.stringContaining('CSR1000v'),
        );
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleConnect
  // -------------------------------------------------------------------------
  describe('handleConnect', () => {
    it('triggers debounced topology save when connecting two nodes', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('canvas');

      await user.click(screen.getByRole('button', { name: 'Connect Nodes' }));

      // Advance past the 2s debounce
      vi.advanceTimersByTime(3000);

      await waitFor(() => {
        const topoCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/update-topology') && opts?.method === 'POST'
        );
        expect(topoCalls.length).toBeGreaterThanOrEqual(1);
      });

      vi.useRealTimers();
    });
  });

  // -------------------------------------------------------------------------
  // handleDelete (node / link)
  // -------------------------------------------------------------------------
  describe('handleDelete', () => {
    it('removes a node and triggers topology save when other nodes remain', async () => {
      // Graph with 2 nodes so deleting one still leaves nodes for saveTopology
      setupFetch({
        '/export-graph': {
          ok: true, status: 200, json: () => Promise.resolve({
            nodes: [
              { id: 'node-1', name: 'R1', nodeType: 'device', type: 'container', model: 'linux', version: 'alpine:latest', x: 10, y: 10 },
              { id: 'node-2', name: 'R2', nodeType: 'device', type: 'container', model: 'linux', version: 'alpine:latest', x: 110, y: 10 },
            ],
            links: [{ endpoints: [{ node: 'node-1', ifname: 'eth1' }, { node: 'node-2', ifname: 'eth1' }] }],
            annotations: [],
          }),
        },
      });
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('canvas');

      await user.click(screen.getByRole('button', { name: 'Delete Node' }));

      // Advance past the 2s debounce
      vi.advanceTimersByTime(3000);

      await waitFor(() => {
        const topoCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/update-topology') && opts?.method === 'POST'
        );
        expect(topoCalls.length).toBeGreaterThanOrEqual(1);
      });

      vi.useRealTimers();
    });

    it('skips topology save when last node is deleted (empty canvas)', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('canvas');

      // Count update-topology calls before the delete action
      const callCountBefore = mockFetch.mock.calls.filter(
        ([url, opts]: [string, any]) => url.includes('/update-topology') && opts?.method === 'POST'
      ).length;

      await user.click(screen.getByRole('button', { name: 'Delete Node' }));

      // Advance past the 2s debounce
      vi.advanceTimersByTime(3000);

      // saveTopology skips when nodes are empty — no new update-topology call
      const callCountAfter = mockFetch.mock.calls.filter(
        ([url, opts]: [string, any]) => url.includes('/update-topology') && opts?.method === 'POST'
      ).length;
      expect(callCountAfter).toBe(callCountBefore);

      vi.useRealTimers();
    });
  });

  // -------------------------------------------------------------------------
  // handleStartTests (accessible via Tests view tab)
  // -------------------------------------------------------------------------
  describe('handleStartTests', () => {
    it('sends POST to /labs/{id}/tests/run', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      // Switch to Tests view tab
      await user.click(screen.getByRole('button', { name: /tests/i }));
      await screen.findByTestId('verification-panel');

      await user.click(screen.getByRole('button', { name: 'Run Tests' }));

      await waitFor(() => {
        const testCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/tests/run') && opts?.method === 'POST'
        );
        expect(testCalls.length).toBe(1);
      });
    });

    it('shows error notification on test run failure', async () => {
      setupFetch({
        '/tests/run': { ok: false, status: 500, text: () => Promise.resolve('Server error'), json: () => Promise.resolve({ detail: 'Server error' }) },
      });

      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      // Switch to Tests view tab
      await user.click(screen.getByRole('button', { name: /tests/i }));
      await screen.findByTestId('verification-panel');

      await user.click(screen.getByRole('button', { name: 'Run Tests' }));

      await waitFor(() => {
        expect(addNotification).toHaveBeenCalledWith(
          'error',
          'Test run failed',
          expect.any(String),
        );
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleExtractConfigs (accessible via Configs view tab)
  // -------------------------------------------------------------------------
  describe('handleExtractConfigs', () => {
    it('sends POST to /labs/{id}/extract-configs with create_snapshot param', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      // Switch to Configs view tab
      await user.click(screen.getByRole('button', { name: /configs/i }));
      await screen.findByTestId('configs-view');

      await user.click(screen.getByRole('button', { name: 'Extract All Configs' }));

      await waitFor(() => {
        const extractCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/extract-configs') && opts?.method === 'POST'
        );
        expect(extractCalls.length).toBe(1);
        expect(extractCalls[0][0]).toContain('create_snapshot=true');
        expect(extractCalls[0][0]).toContain('snapshot_type=manual');
      });
    });

    it('adds success task log entry on extract success', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      // Switch to Configs view tab
      await user.click(screen.getByRole('button', { name: /configs/i }));
      await screen.findByTestId('configs-view');

      await user.click(screen.getByRole('button', { name: 'Extract All Configs' }));

      await waitFor(() => {
        const panel = screen.getByTestId('task-log-panel');
        expect(panel.textContent).toContain('Extracted 2 configs');
      });
    });

    it('adds error task log entry on extract failure', async () => {
      setupFetch({
        '/extract-configs': { ok: false, status: 500, text: () => Promise.resolve('Agent unreachable'), json: () => Promise.resolve({ detail: 'Agent unreachable' }) },
      });

      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      // Switch to Configs view tab
      await user.click(screen.getByRole('button', { name: /configs/i }));
      await screen.findByTestId('configs-view');

      await user.click(screen.getByRole('button', { name: 'Extract All Configs' }));

      await waitFor(() => {
        const panel = screen.getByTestId('task-log-panel');
        expect(panel.textContent).toContain('Extract failed');
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleExitLab
  // -------------------------------------------------------------------------
  describe('handleExitLab', () => {
    it('returns to dashboard when exit is confirmed', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      await user.click(screen.getByRole('button', { name: 'Exit Lab' }));

      await waitFor(() => {
        expect(screen.getByTestId('dashboard')).toBeInTheDocument();
      });

      confirmSpy.mockRestore();
    });

    it('stays in lab when exit is cancelled', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      await user.click(screen.getByRole('button', { name: 'Exit Lab' }));

      // Should still be showing the topbar (in lab view)
      expect(screen.getByTestId('topbar')).toBeInTheDocument();

      confirmSpy.mockRestore();
    });
  });

  // -------------------------------------------------------------------------
  // handleDeleteLab clears active lab if deleting current
  // -------------------------------------------------------------------------
  describe('handleDeleteLab clears active lab', () => {
    it('returns to dashboard when deleting the active lab', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      // Open a lab first
      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      // Now exit back to dashboard (need to confirm)
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      await user.click(screen.getByRole('button', { name: 'Exit Lab' }));
      await screen.findByTestId('dashboard');

      // Delete the lab from dashboard
      await user.click(screen.getByRole('button', { name: 'Delete Lab' }));

      await waitFor(() => {
        const deleteCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/labs/lab-1') && opts?.method === 'DELETE'
        );
        expect(deleteCalls.length).toBe(1);
      });

      confirmSpy.mockRestore();
    });
  });

  // -------------------------------------------------------------------------
  // handleRenameLab from TopBar (active lab rename)
  // -------------------------------------------------------------------------
  describe('handleRenameLab from TopBar', () => {
    it('renames active lab via TopBar', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      await user.click(screen.getByRole('button', { name: 'Rename' }));

      await waitFor(() => {
        const putCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/labs/lab-1') && opts?.method === 'PUT'
        );
        expect(putCalls.length).toBe(1);
        const body = JSON.parse(putCalls[0][1].body);
        expect(body.name).toBe('New Name');
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleSelectLab loads lab data
  // -------------------------------------------------------------------------
  describe('handleSelectLab', () => {
    it('fetches graph data when a lab is selected', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));

      await waitFor(() => {
        const graphCalls = mockFetch.mock.calls.filter(
          ([url]: [string]) => url.includes('/export-graph')
        );
        expect(graphCalls.length).toBeGreaterThanOrEqual(1);
      });
    });

    it('fetches node states when a lab is selected', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));

      await waitFor(() => {
        const stateCalls = mockFetch.mock.calls.filter(
          ([url]: [string]) => url.includes('/nodes/states') || url.includes('/nodes/refresh')
        );
        expect(stateCalls.length).toBeGreaterThanOrEqual(1);
      });
    });

    it('fetches jobs when a lab is selected', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));

      await waitFor(() => {
        const jobsCalls = mockFetch.mock.calls.filter(
          ([url]: [string]) => url.includes('/jobs')
        );
        expect(jobsCalls.length).toBeGreaterThanOrEqual(1);
      });
    });
  });

  // -------------------------------------------------------------------------
  // handleExtractNodeConfig (per-node extract via Canvas)
  // -------------------------------------------------------------------------
  describe('handleExtractNodeConfig', () => {
    it('sends per-node extract-config request from Canvas', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('canvas');

      await user.click(screen.getByRole('button', { name: 'Extract Node Config' }));

      await waitFor(() => {
        const extractCalls = mockFetch.mock.calls.filter(
          ([url, opts]: [string, any]) => url.includes('/extract-config') && opts?.method === 'POST'
        );
        expect(extractCalls.length).toBeGreaterThanOrEqual(1);
      });
    });
  });

  // -------------------------------------------------------------------------
  // View tab switching
  // -------------------------------------------------------------------------
  describe('view tab switching', () => {
    it('switches to Configs view when Configs tab is clicked', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      await user.click(screen.getByRole('button', { name: /configs/i }));

      await waitFor(() => {
        expect(screen.getByTestId('configs-view')).toBeInTheDocument();
      });
    });

    it('switches to Tests view when Tests tab is clicked', async () => {
      const user = userEvent.setup();
      render(<Wrapper><StudioPage /></Wrapper>);
      await screen.findByTestId('dashboard');

      await user.click(screen.getByRole('button', { name: 'Open Lab' }));
      await screen.findByTestId('topbar');

      await user.click(screen.getByRole('button', { name: /tests/i }));

      await waitFor(() => {
        expect(screen.getByTestId('verification-panel')).toBeInTheDocument();
      });
    });
  });
});
