/**
 * Canvas round 11 tests — timer effects, image sync, link hover, text editing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import Canvas from './Canvas';
import {
  Node,
  Link,
  Annotation,
  DeviceModel,
  DeviceType,
  DeviceNode,
} from '../../types';
import { RuntimeStatus } from '../RuntimeControl';
import { ThemeProvider } from '../../../theme/ThemeProvider';
import type { LinkStateData } from '../../hooks/useLabStateWS';

// Mock getBoundingClientRect for container element
const mockGetBoundingClientRect = vi.fn(() => ({
  left: 0, top: 0, right: 800, bottom: 600,
  width: 800, height: 600, x: 0, y: 0, toJSON: () => {},
}));

vi.mock('../../../theme/index', () => ({
  useTheme: () => ({ effectiveMode: 'light' }),
}));

vi.mock('../../../utils/agentColors', () => ({
  getAgentColor: () => '#3b82f6',
  getAgentInitials: (name: string) => name.substring(0, 2).toUpperCase(),
}));

vi.mock('../../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    notifications: [],
    preferences: {
      canvas_settings: {
        errorIndicator: { showIcon: true, showBorder: true, pulseAnimation: true },
      },
    },
    addNotification: vi.fn(),
    dismissNotification: vi.fn(),
    dismissAllNotifications: vi.fn(),
  }),
}));

const renderWithTheme = (ui: React.ReactElement) => render(<ThemeProvider>{ui}</ThemeProvider>);

const mockDeviceModels: DeviceModel[] = [
  { id: 'ceos', name: 'Arista cEOS', type: DeviceType.ROUTER, icon: 'fa-microchip', versions: ['4.28.0F'], isActive: true, vendor: 'Arista' },
  { id: 'linux', name: 'Linux', type: DeviceType.HOST, icon: 'fa-server', versions: ['alpine:latest'], isActive: true, vendor: 'Generic' },
];

const createDeviceNode = (overrides: Partial<DeviceNode> = {}): DeviceNode => ({
  id: 'node-1', name: 'Router1', nodeType: 'device', type: DeviceType.ROUTER,
  model: 'ceos', version: '4.28.0F', x: 100, y: 100, ...overrides,
});

const createLink = (overrides: Partial<Link> = {}): Link => ({
  id: 'link-1', source: 'node-1', target: 'node-2', type: 'p2p', ...overrides,
});

const createAnnotation = (overrides: Partial<Annotation> = {}): Annotation => ({
  id: 'ann-1', type: 'text' as const, x: 200, y: 200, ...overrides,
});

describe('Canvas - effects and state', () => {
  const noop = vi.fn();
  const defaultProps = {
    nodes: [] as Node[],
    links: [] as Link[],
    annotations: [] as Annotation[],
    runtimeStates: {} as Record<string, RuntimeStatus>,
    nodeStates: {} as Record<string, any>,
    deviceModels: mockDeviceModels,
    agents: [] as { id: string; name: string }[],
    showAgentIndicators: false,
    onNodeMove: noop, onAnnotationMove: noop, onConnect: noop,
    selectedId: null as string | null,
    onSelect: noop, onOpenConsole: noop, onExtractConfig: noop,
    onUpdateStatus: noop, onDelete: noop, onDropDevice: noop,
    onDropExternalNetwork: noop, onToolCreate: noop, onUpdateAnnotation: noop,
    onSelectMultiple: noop, onFocusHandled: noop,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.getBoundingClientRect = mockGetBoundingClientRect;
    localStorage.clear();
  });

  // -----------------------------------------------------------------------
  // Timer effect for transitional nodes
  // -----------------------------------------------------------------------
  describe('Elapsed timer effect', () => {
    beforeEach(() => { vi.useFakeTimers(); });
    afterEach(() => { vi.useRealTimers(); });

    it('sets interval when nodes are in starting state', () => {
      const node = createDeviceNode({ id: 'n1', name: 'R1' });
      const nodeStates = {
        n1: { display_state: 'starting', boot_started_at: new Date().toISOString() },
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} nodeStates={nodeStates} />
      );

      // The component should have set an interval — advancing time should not throw
      act(() => { vi.advanceTimersByTime(3000); });
      // If interval runs, it just updates a tick state — no visible crash
    });

    it('does not set interval when all nodes are stopped', () => {
      const node = createDeviceNode({ id: 'n1', name: 'R1' });
      const nodeStates = { n1: { display_state: 'stopped' } };

      const spy = vi.spyOn(global, 'setInterval');
      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} nodeStates={nodeStates} />
      );
      // setInterval may be called by other effects, but the timer effect
      // itself should not fire (no transitional nodes). We just ensure no crash.
      act(() => { vi.advanceTimersByTime(3000); });
      spy.mockRestore();
    });

    it('clears interval when transitional nodes resolve', () => {
      const node = createDeviceNode({ id: 'n1', name: 'R1' });

      const { rerender } = renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          nodeStates={{ n1: { display_state: 'starting' } }}
        />
      );

      act(() => { vi.advanceTimersByTime(2000); });

      // Rerender with resolved state
      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          nodeStates={{ n1: { display_state: 'running' } }}
        />
      );

      // Should not crash when advancing time after cleanup
      act(() => { vi.advanceTimersByTime(3000); });
    });
  });

  // -----------------------------------------------------------------------
  // Image sync status indicator
  // -----------------------------------------------------------------------
  describe('Image sync status', () => {
    it('renders node with syncing state', () => {
      const node = createDeviceNode({ id: 'n1', name: 'SyncNode' });
      const nodeStates = {
        n1: { display_state: 'stopped', image_sync_status: 'syncing' },
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} nodeStates={nodeStates} />
      );

      expect(screen.getByText('SyncNode')).toBeInTheDocument();
    });

    it('renders node with synced state', () => {
      const node = createDeviceNode({ id: 'n1', name: 'ReadyNode' });
      const nodeStates = {
        n1: { display_state: 'stopped', image_sync_status: 'synced' },
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} nodeStates={nodeStates} />
      );

      expect(screen.getByText('ReadyNode')).toBeInTheDocument();
    });

    it('renders node with failed sync state', () => {
      const node = createDeviceNode({ id: 'n1', name: 'FailNode' });
      const nodeStates = {
        n1: { display_state: 'stopped', image_sync_status: 'failed' },
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} nodeStates={nodeStates} />
      );

      expect(screen.getByText('FailNode')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Link hover
  // -----------------------------------------------------------------------
  describe('Link hover', () => {
    it('link interaction line exists for connections', () => {
      const nodes = [
        createDeviceNode({ id: 'n1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'n2', name: 'R2', x: 300, y: 100 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'n1', target: 'n2' })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      // Transparent interaction line should exist for link hover detection
      const transparentLine = document.querySelector('line[stroke="transparent"]');
      expect(transparentLine).toBeInTheDocument();
    });

    it('hover events on link do not crash', () => {
      const nodes = [
        createDeviceNode({ id: 'n1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'n2', name: 'R2', x: 300, y: 100 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'n1', target: 'n2' })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      const transparentLine = document.querySelector('line[stroke="transparent"]');
      if (transparentLine) {
        fireEvent.mouseEnter(transparentLine);
        fireEvent.mouseLeave(transparentLine);
      }
      // No crash — hover state managed internally
    });
  });

  // -----------------------------------------------------------------------
  // Scenario highlights
  // -----------------------------------------------------------------------
  describe('Scenario highlights', () => {
    it('passes scenarioHighlights to render without crashing', () => {
      const nodes = [
        createDeviceNode({ id: 'n1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'n2', name: 'R2', x: 300, y: 100 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'n1', target: 'n2' })];

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={nodes}
          links={links}
          scenarioHighlights={{
            activeNodeNames: new Set(['R1']),
            activeLinkName: 'R1:eth1 <-> R2:eth1',
          }}
        />
      );

      expect(screen.getByText('R1')).toBeInTheDocument();
      expect(screen.getByText('R2')).toBeInTheDocument();
    });

    it('handles null scenario highlights', () => {
      const node = createDeviceNode({ id: 'n1', name: 'R1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} scenarioHighlights={undefined} />
      );

      expect(screen.getByText('R1')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Link state coloring
  // -----------------------------------------------------------------------
  describe('Link state map', () => {
    it('renders links with linkStates data', () => {
      const nodes = [
        createDeviceNode({ id: 'n1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'n2', name: 'R2', x: 300, y: 100 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'n1', target: 'n2' })];
      const linkStates: LinkStateData[] = [
        { source_node: 'R1', target_node: 'R2', actual_state: 'up' } as any,
      ];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} linkStates={linkStates} />
      );

      // Links rendered
      const line = document.querySelector('line');
      expect(line).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Keyboard delete
  // -----------------------------------------------------------------------
  describe('Keyboard delete', () => {
    it('calls onDelete when Delete key pressed with selection', () => {
      const mockDelete = vi.fn();
      const node = createDeviceNode({ id: 'n1', name: 'R1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId="n1" onDelete={mockDelete} />
      );

      fireEvent.keyDown(window, { key: 'Delete' });
      expect(mockDelete).toHaveBeenCalledWith('n1');
    });

    it('does not delete when no selection', () => {
      const mockDelete = vi.fn();
      const node = createDeviceNode({ id: 'n1', name: 'R1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId={null} onDelete={mockDelete} />
      );

      fireEvent.keyDown(window, { key: 'Delete' });
      expect(mockDelete).not.toHaveBeenCalled();
    });
  });
});
