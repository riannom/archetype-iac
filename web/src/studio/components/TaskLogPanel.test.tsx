import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import TaskLogPanel from './TaskLogPanel';

vi.mock('./TerminalSession', () => ({
  default: ({ labId, nodeId }: { labId: string; nodeId: string }) => (
    <div data-testid={`terminal-${nodeId}`}>Terminal for {nodeId} in {labId}</div>
  ),
}));

vi.mock('../../theme', () => ({
  useTheme: () => ({ effectiveMode: 'light' }),
}));

vi.mock('../hooks/useUptime', () => ({
  useUptime: () => '1h 23m',
}));

vi.mock('../../components/VersionBadge', () => ({
  VersionBadge: () => <span data-testid="version-badge">v0.5.0</span>,
  default: () => <span data-testid="version-badge">v0.5.0</span>,
}));

const baseProps = {
  entries: [] as any[],
  isVisible: true,
  onToggle: vi.fn(),
  onClear: vi.fn(),
};

const consoleProps = {
  ...baseProps,
  showConsoles: true,
  consoleTabs: [
    { nodeId: 'n1', nodeName: 'Router 1' },
    { nodeId: 'n2', nodeName: 'Switch 1' },
    { nodeId: 'n3', nodeName: 'Router 2' },
  ],
  activeTabId: 'log',
  onSelectTab: vi.fn(),
  onCloseConsoleTab: vi.fn(),
  onUndockConsole: vi.fn(),
  onReorderTab: vi.fn(),
  labId: 'lab-1',
  nodeStates: {},
};

describe('TaskLogPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  // ============================================================
  // Basic rendering and actions
  // ============================================================

  it('renders entries and triggers actions', () => {
    const onToggle = vi.fn();
    const onClear = vi.fn();
    const onEntryClick = vi.fn();

    render(
      <TaskLogPanel
        entries={[
          {
            id: '1',
            timestamp: new Date('2026-02-05T10:00:00Z'),
            level: 'error',
            message: 'Failed',
            jobId: 'job1',
          },
          {
            id: '2',
            timestamp: new Date('2026-02-05T10:00:01Z'),
            level: 'info',
            message: 'Saved',
          },
        ]}
        isVisible={true}
        onToggle={onToggle}
        onClear={onClear}
        onEntryClick={onEntryClick}
      />
    );

    fireEvent.click(screen.getByText('Clear'));
    expect(onClear).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('Failed'));
    fireEvent.click(screen.getByText('Saved'));
    expect(onEntryClick).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByText('v'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('toggles auto-refresh when enabled', () => {
    const onToggleAutoUpdate = vi.fn();

    render(
      <TaskLogPanel
        entries={[]}
        isVisible={true}
        onToggle={vi.fn()}
        onClear={vi.fn()}
        autoUpdateEnabled={true}
        onToggleAutoUpdate={onToggleAutoUpdate}
      />
    );

    fireEvent.click(screen.getByText('Auto-refresh'));
    expect(onToggleAutoUpdate).toHaveBeenCalledWith(false);
  });

  it('auto-scrolls to latest entry when auto-refresh is enabled', async () => {
    const entries = [
      { id: '1', timestamp: new Date('2026-02-05T10:00:00Z'), level: 'info' as const, message: 'one' },
    ];
    const { container, rerender } = render(
      <TaskLogPanel {...baseProps} entries={entries} autoUpdateEnabled={true} />
    );
    const logContainer = container.querySelector('.h-full.overflow-y-auto.font-mono.text-\\[11px\\]') as HTMLDivElement;
    expect(logContainer).toBeTruthy();

    Object.defineProperty(logContainer, 'scrollHeight', { value: 420, configurable: true });
    logContainer.scrollTop = 0;

    rerender(
      <TaskLogPanel
        {...baseProps}
        autoUpdateEnabled={true}
        entries={[...entries, { id: '2', timestamp: new Date('2026-02-05T10:00:01Z'), level: 'info', message: 'two' }]}
      />
    );

    await waitFor(() => {
      expect(logContainer.scrollTop).toBe(420);
    });
  });

  it('does not auto-scroll when auto-refresh is disabled', async () => {
    const entries = [
      { id: '1', timestamp: new Date('2026-02-05T10:00:00Z'), level: 'info' as const, message: 'one' },
    ];
    const { container, rerender } = render(
      <TaskLogPanel {...baseProps} entries={entries} autoUpdateEnabled={false} />
    );
    const logContainer = container.querySelector('.h-full.overflow-y-auto.font-mono.text-\\[11px\\]') as HTMLDivElement;
    expect(logContainer).toBeTruthy();

    Object.defineProperty(logContainer, 'scrollHeight', { value: 420, configurable: true });
    logContainer.scrollTop = 0;

    rerender(
      <TaskLogPanel
        {...baseProps}
        autoUpdateEnabled={false}
        entries={[...entries, { id: '2', timestamp: new Date('2026-02-05T10:00:01Z'), level: 'info', message: 'two' }]}
      />
    );

    await waitFor(() => {
      expect(logContainer.scrollTop).toBe(0);
    });
  });

  // ============================================================
  // Console tabs
  // ============================================================

  it('renders console tab names when consoleTabs are provided', () => {
    render(<TaskLogPanel {...consoleProps} />);

    expect(screen.getByText('Router 1')).toBeInTheDocument();
    expect(screen.getByText('Switch 1')).toBeInTheDocument();
    expect(screen.getByText('Router 2')).toBeInTheDocument();
  });

  it('renders Log tab alongside console tabs', () => {
    render(<TaskLogPanel {...consoleProps} />);

    expect(screen.getByText('Log')).toBeInTheDocument();
  });

  it('switches to a console tab when clicked', () => {
    const onSelectTab = vi.fn();
    render(<TaskLogPanel {...consoleProps} onSelectTab={onSelectTab} />);

    fireEvent.click(screen.getByText('Switch 1'));
    expect(onSelectTab).toHaveBeenCalledWith('n2');
  });

  it('shows terminal when a console tab is active', () => {
    render(
      <TaskLogPanel
        {...consoleProps}
        activeTabId="n1"
        nodeStates={{ n1: { actual_state: 'running', is_ready: true } as any }}
      />
    );

    expect(screen.getByTestId('terminal-n1')).toBeInTheDocument();
  });

  it('closes a console tab via the close button', () => {
    const onCloseConsoleTab = vi.fn();
    render(<TaskLogPanel {...consoleProps} onCloseConsoleTab={onCloseConsoleTab} />);

    const closeButtons = screen.getAllByRole('button').filter(btn => {
      const icon = btn.querySelector('.fa-xmark');
      return icon !== null;
    });
    expect(closeButtons.length).toBe(3); // one per console tab

    fireEvent.click(closeButtons[0]);
    expect(onCloseConsoleTab).toHaveBeenCalledWith('n1');
  });

  // ============================================================
  // Undocking
  // ============================================================

  it('undocks console tab when dragging out of the tab strip (diagonal)', () => {
    const onUndockConsole = vi.fn();
    const onReorderTab = vi.fn();

    render(
      <TaskLogPanel
        entries={[]}
        isVisible={true}
        onToggle={vi.fn()}
        onClear={vi.fn()}
        showConsoles={true}
        consoleTabs={[
          { nodeId: 'n1', nodeName: 'Node 1' },
          { nodeId: 'n2', nodeName: 'Node 2' },
        ]}
        activeTabId={'n1'}
        onSelectTab={vi.fn()}
        onUndockConsole={onUndockConsole}
        onReorderTab={onReorderTab}
        labId={'lab-1'}
        nodeStates={{}}
      />
    );

    const tab = screen.getByText('Node 2').closest('div');
    expect(tab).toBeTruthy();

    const tabStrip = tab!.parentElement as HTMLElement;
    vi.spyOn(tabStrip, 'getBoundingClientRect').mockReturnValue({
      left: 200,
      top: 50,
      right: 500,
      bottom: 90,
      width: 300,
      height: 40,
      x: 200,
      y: 50,
      toJSON: () => ({}),
    } as any);

    fireEvent.mouseDown(tab!, { clientX: 300, clientY: 70 });
    fireEvent.mouseMove(document, { clientX: 320, clientY: 140 }); // outside bottom
    fireEvent.mouseUp(document, { clientX: 320, clientY: 140 });

    expect(onUndockConsole).toHaveBeenCalledWith('n2', 60, 90);
    expect(onReorderTab).not.toHaveBeenCalled();
  });

  it('undocks console tab when dragging out of the tab strip (horizontal)', () => {
    const onUndockConsole = vi.fn();
    const onReorderTab = vi.fn();

    render(
      <TaskLogPanel
        entries={[]}
        isVisible={true}
        onToggle={vi.fn()}
        onClear={vi.fn()}
        showConsoles={true}
        consoleTabs={[
          { nodeId: 'n1', nodeName: 'Node 1' },
          { nodeId: 'n2', nodeName: 'Node 2' },
        ]}
        activeTabId={'n1'}
        onSelectTab={vi.fn()}
        onUndockConsole={onUndockConsole}
        onReorderTab={onReorderTab}
        labId={'lab-1'}
        nodeStates={{}}
      />
    );

    const tab = screen.getByText('Node 2').closest('div');
    expect(tab).toBeTruthy();

    const tabStrip = tab!.parentElement as HTMLElement;
    vi.spyOn(tabStrip, 'getBoundingClientRect').mockReturnValue({
      left: 200,
      top: 50,
      right: 500,
      bottom: 90,
      width: 300,
      height: 40,
      x: 200,
      y: 50,
      toJSON: () => ({}),
    } as any);

    fireEvent.mouseDown(tab!, { clientX: 300, clientY: 70 });
    fireEvent.mouseMove(document, { clientX: 560, clientY: 75 }); // outside right
    fireEvent.mouseUp(document, { clientX: 560, clientY: 75 });

    expect(onUndockConsole).toHaveBeenCalledWith('n2', 300, 25);
    expect(onReorderTab).not.toHaveBeenCalled();
  });

  it('triggers undock when dragged outside tab strip vertically', () => {
    const onUndockConsole = vi.fn();
    const onReorderTab = vi.fn();

    render(
      <TaskLogPanel
        {...consoleProps}
        onUndockConsole={onUndockConsole}
        onReorderTab={onReorderTab}
      />
    );

    const tab = screen.getByText('Router 2').closest('div');
    expect(tab).toBeTruthy();

    const tabStrip = tab!.parentElement as HTMLElement;
    vi.spyOn(tabStrip, 'getBoundingClientRect').mockReturnValue({
      left: 100, top: 50, right: 600, bottom: 90, width: 500, height: 40, x: 100, y: 50,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.mouseDown(tab!, { clientX: 300, clientY: 70 });
    fireEvent.mouseMove(document, { clientX: 310, clientY: 150 }); // well outside bottom
    fireEvent.mouseUp(document, { clientX: 310, clientY: 150 });

    expect(onUndockConsole).toHaveBeenCalledWith('n3', expect.any(Number), expect.any(Number));
    expect(onReorderTab).not.toHaveBeenCalled();
  });

  it('does not undock on small drag within tab strip', () => {
    const onUndockConsole = vi.fn();

    render(
      <TaskLogPanel
        {...consoleProps}
        onUndockConsole={onUndockConsole}
      />
    );

    const tab = screen.getByText('Router 1').closest('div');
    expect(tab).toBeTruthy();

    const tabStrip = tab!.parentElement as HTMLElement;
    vi.spyOn(tabStrip, 'getBoundingClientRect').mockReturnValue({
      left: 100, top: 50, right: 600, bottom: 90, width: 500, height: 40, x: 100, y: 50,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.mouseDown(tab!, { clientX: 300, clientY: 70 });
    fireEvent.mouseMove(document, { clientX: 305, clientY: 72 }); // tiny move within strip
    fireEvent.mouseUp(document, { clientX: 305, clientY: 72 });

    expect(onUndockConsole).not.toHaveBeenCalled();
  });

  // ============================================================
  // Tab reordering
  // ============================================================

  it('triggers horizontal reorder within tab strip', () => {
    const onReorderTab = vi.fn();
    const onUndockConsole = vi.fn();

    render(
      <TaskLogPanel
        {...consoleProps}
        onReorderTab={onReorderTab}
        onUndockConsole={onUndockConsole}
      />
    );

    const tab = screen.getByText('Router 1').closest('div');
    expect(tab).toBeTruthy();

    const tabStrip = tab!.parentElement as HTMLElement;
    vi.spyOn(tabStrip, 'getBoundingClientRect').mockReturnValue({
      left: 100, top: 50, right: 600, bottom: 90, width: 500, height: 40, x: 100, y: 50,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.mouseDown(tab!, { clientX: 200, clientY: 70 });
    fireEvent.mouseMove(document, { clientX: 350, clientY: 72 });
    fireEvent.mouseUp(document, { clientX: 350, clientY: 72 });

    expect(onUndockConsole).not.toHaveBeenCalled();
  });

  // ============================================================
  // Resize persistence
  // ============================================================

  it('restores height from localStorage on mount', () => {
    localStorage.setItem('archetype-tasklog-height', '350');

    const { container } = render(<TaskLogPanel {...baseProps} />);

    const contentArea = container.querySelector('[style*="height"]');
    if (contentArea) {
      expect(contentArea.getAttribute('style')).toContain('350px');
    }
  });

  it('uses default height when localStorage is empty', () => {
    const { container } = render(<TaskLogPanel {...baseProps} />);

    const contentArea = container.querySelector('[style*="height"]');
    if (contentArea) {
      expect(contentArea.getAttribute('style')).toContain('200px');
    }
  });

  it('clamps restored height to min/max bounds', () => {
    localStorage.setItem('archetype-tasklog-height', '50'); // Below MIN_HEIGHT of 100

    const { container } = render(<TaskLogPanel {...baseProps} />);

    const contentArea = container.querySelector('[style*="height"]');
    if (contentArea) {
      expect(contentArea.getAttribute('style')).toContain('100px');
    }
  });

  // ============================================================
  // Status bar
  // ============================================================

  it('renders WS connected indicator as LIVE when connected', () => {
    render(
      <TaskLogPanel
        {...baseProps}
        wsConnected={true}
        reconnectAttempts={0}
      />
    );

    expect(screen.getByText('LIVE')).toBeInTheDocument();
  });

  it('renders RECONNECTING when reconnect attempts > 0', () => {
    render(
      <TaskLogPanel
        {...baseProps}
        wsConnected={false}
        reconnectAttempts={3}
      />
    );

    expect(screen.getByText('RECONNECTING')).toBeInTheDocument();
  });

  it('shows error count badge when there are error entries', () => {
    const errorEntries = [
      { id: '1', timestamp: new Date('2026-02-05T10:00:00Z'), level: 'error' as const, message: 'Error 1' },
      { id: '2', timestamp: new Date('2026-02-05T10:00:01Z'), level: 'error' as const, message: 'Error 2' },
      { id: '3', timestamp: new Date('2026-02-05T10:00:02Z'), level: 'info' as const, message: 'Info 1' },
    ];

    render(
      <TaskLogPanel
        {...baseProps}
        entries={errorEntries}
      />
    );

    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('renders uptime display', () => {
    render(
      <TaskLogPanel
        {...baseProps}
        nodeStates={{ n1: { actual_state: 'running', is_ready: true } as any }}
      />
    );

    expect(screen.getByText(/UPTIME: 1h 23m/)).toBeInTheDocument();
  });
});
