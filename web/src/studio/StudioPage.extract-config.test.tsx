import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import StudioPage from './StudioPage';

const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

vi.mock('../theme/index', async () => {
  const actual = await vi.importActual('../theme/index');
  return {
    ...actual,
    useTheme: () => ({
      effectiveMode: 'light',
      mode: 'light',
      setMode: vi.fn(),
      toggleMode: vi.fn(),
    }),
  };
});

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    notifications: [],
    addNotification: vi.fn(),
    dismissNotification: vi.fn(),
    dismissAllNotifications: vi.fn(),
  }),
}));

vi.mock('../contexts/ImageLibraryContext', () => ({
  useImageLibrary: () => ({
    imageLibrary: [],
    loading: false,
    error: null,
    refreshImageLibrary: vi.fn(),
  }),
  ImageLibraryProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock('../contexts/DeviceCatalogContext', () => ({
  useDeviceCatalog: () => ({
    vendorCategories: [],
    imageCatalog: {},
    deviceModels: [],
    deviceCategories: [],
    addCustomDevice: vi.fn(),
    removeCustomDevice: vi.fn(),
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
  DeviceCatalogProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock('./hooks/useLabStateWS', () => ({
  default: () => ({
    connected: false,
    reconnectAttempts: 0,
    sendMessage: vi.fn(),
  }),
}));

vi.mock('./components/Dashboard', () => ({
  default: ({ onSelect }: { onSelect: (lab: { id: string; name: string }) => void }) => (
    <button onClick={() => onSelect({ id: 'lab-1', name: 'Test Lab 1' })}>Open Lab</button>
  ),
}));

vi.mock('./components/Canvas', () => ({
  default: ({ onExtractConfig }: { onExtractConfig?: (nodeId: string) => void }) => (
    <button onClick={() => onExtractConfig?.('node-1')}>Trigger Node Extract</button>
  ),
}));

vi.mock('./components/TaskLogPanel', () => ({
  __esModule: true,
  default: ({ entries }: { entries: Array<{ id: string; message: string }> }) => (
    <div data-testid="task-log-panel">
      {entries.map((entry) => (
        <div key={entry.id}>{entry.message}</div>
      ))}
    </div>
  ),
  TaskLogEntry: {},
  DockedConsole: {},
}));

vi.mock('./components/TopBar', () => ({ default: () => null }));
vi.mock('./components/Sidebar', () => ({ default: () => null }));
vi.mock('./components/PropertiesPanel', () => ({ default: () => null }));
vi.mock('./components/StatusBar', () => ({ default: () => null }));
vi.mock('./components/ConsoleManager', () => ({ default: () => null }));
vi.mock('./components/AgentAlertBanner', () => ({ default: () => null }));
vi.mock('./components/SystemStatusStrip', () => ({ default: () => null }));
vi.mock('./components/RuntimeControl', () => ({ default: () => null }));
vi.mock('./components/ConfigsView', () => ({ default: () => null }));
vi.mock('./components/LogsView', () => ({ default: () => null }));
vi.mock('./components/ConfigViewerModal', () => ({ default: () => null }));
vi.mock('./components/JobLogModal', () => ({ default: () => null }));

const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter>{children}</BrowserRouter>
);

function setupFetch(extractResponse: { ok: boolean; body: unknown; status?: number }) {
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes('/auth/me')) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve({ id: 'u1', username: 'test', global_role: 'super_admin' }),
      };
    }
    if (url.includes('/labs') && !url.includes('/export-graph')) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve({ labs: [{ id: 'lab-1', name: 'Test Lab 1' }] }),
      };
    }
    if (url.includes('/images/library')) {
      return { ok: true, status: 200, json: () => Promise.resolve({ images: [] }) };
    }
    if (url.includes('/dashboard/metrics')) {
      return {
        ok: true,
        status: 200,
        json: () => Promise.resolve({
          agents: { online: 1, total: 1 },
          containers: { running: 0, total: 0 },
          cpu_percent: 1,
          memory_percent: 1,
          labs_running: 0,
          labs_total: 1,
        }),
      };
    }
    if (url.includes('/agents')) {
      return { ok: true, status: 200, json: () => Promise.resolve([]) };
    }
    if (url.includes('/export-graph')) {
      return {
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            nodes: [
              {
                id: 'node-1',
                name: 'iosv-6',
                nodeType: 'device',
                type: 'router',
                model: 'cisco_iosv_6',
                version: '15.9',
                x: 10,
                y: 10,
              },
            ],
            links: [],
            annotations: [],
          }),
      };
    }
    if (url.includes('/nodes/states')) {
      return { ok: true, status: 200, json: () => Promise.resolve({ nodes: [] }) };
    }
    if (url.includes('/jobs')) {
      return { ok: true, status: 200, json: () => Promise.resolve({ jobs: [] }) };
    }
    if (url.includes('/extract-config')) {
      return {
        ok: extractResponse.ok,
        status: extractResponse.status ?? (extractResponse.ok ? 200 : 500),
        json: () => Promise.resolve(extractResponse.body),
      };
    }
    return { ok: true, status: 200, json: () => Promise.resolve({}) };
  });
}

describe('StudioPage node extract config task log', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('token', 'test-token');
  });

  it('adds exactly one success task log entry for node extract success', async () => {
    setupFetch({ ok: true, body: { message: 'snapshot created' } });
    const user = userEvent.setup();
    render(
      <Wrapper>
        <StudioPage />
      </Wrapper>
    );

    await user.click(screen.getByRole('button', { name: 'Open Lab' }));
    await user.click(screen.getByRole('button', { name: 'Trigger Node Extract' }));

    await waitFor(() => {
      const message = 'Config extracted successfully for "iosv-6"';
      const successMatches = screen.getAllByText(message);
      expect(successMatches).toHaveLength(1);
    });
  });

  it('adds exactly one failure task log entry for node extract failure', async () => {
    setupFetch({ ok: false, status: 500, body: { detail: 'agent timeout' } });
    const user = userEvent.setup();
    render(
      <Wrapper>
        <StudioPage />
      </Wrapper>
    );

    await user.click(screen.getByRole('button', { name: 'Open Lab' }));
    await user.click(screen.getByRole('button', { name: 'Trigger Node Extract' }));

    await waitFor(() => {
      const failurePrefix = 'Config extraction failed for "iosv-6"';
      const failures = screen.getAllByText((content) => content.startsWith(failurePrefix));
      expect(failures).toHaveLength(1);
    });
  });
});
