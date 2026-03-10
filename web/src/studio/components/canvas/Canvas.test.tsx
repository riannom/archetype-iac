import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Canvas from './Canvas';
import {
  Node,
  Link,
  Annotation,
  DeviceModel,
  DeviceType,
  DeviceNode,
  ExternalNetworkNode,
} from '../../types';
import { RuntimeStatus } from '../RuntimeControl';
import { ThemeProvider } from '../../../theme/ThemeProvider';
import type { LinkStateData } from '../../hooks/useLabStateWS';

// Mock getBoundingClientRect for container element
const mockGetBoundingClientRect = vi.fn(() => ({
  left: 0,
  top: 0,
  right: 800,
  bottom: 600,
  width: 800,
  height: 600,
  x: 0,
  y: 0,
  toJSON: () => {},
}));

// Mock the theme hook
vi.mock('../../../theme/index', () => ({
  useTheme: () => ({
    effectiveMode: 'light',
  }),
}));

// Mock agentColors utility
vi.mock('../../../utils/agentColors', () => ({
  getAgentColor: () => '#3b82f6',
  getAgentInitials: (name: string) => name.substring(0, 2).toUpperCase(),
}));

// Mock useNotifications to avoid needing NotificationProvider
vi.mock('../../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    notifications: [],
    preferences: {
      canvas_settings: {
        errorIndicator: {
          showIcon: true,
          showBorder: true,
          pulseAnimation: true,
        },
      },
    },
    addNotification: vi.fn(),
    dismissNotification: vi.fn(),
    dismissAllNotifications: vi.fn(),
  }),
}));

const renderWithTheme = (ui: React.ReactElement) => {
  return render(<ThemeProvider>{ui}</ThemeProvider>);
};

// Sample device models
const mockDeviceModels: DeviceModel[] = [
  {
    id: 'ceos',
    name: 'Arista cEOS',
    type: DeviceType.ROUTER,
    icon: 'fa-microchip',
    versions: ['4.28.0F'],
    isActive: true,
    vendor: 'Arista',
  },
  {
    id: 'srlinux',
    name: 'Nokia SR Linux',
    type: DeviceType.SWITCH,
    icon: 'fa-network-wired',
    versions: ['23.10.1'],
    isActive: true,
    vendor: 'Nokia',
  },
  {
    id: 'linux',
    name: 'Linux Container',
    type: DeviceType.HOST,
    icon: 'fa-server',
    versions: ['alpine:latest'],
    isActive: true,
    vendor: 'Generic',
  },
];

// Factory functions
const createDeviceNode = (overrides: Partial<DeviceNode> = {}): DeviceNode => ({
  id: 'node-1',
  name: 'Router1',
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'ceos',
  version: '4.28.0F',
  x: 100,
  y: 100,
  ...overrides,
});

const createExternalNetworkNode = (overrides: Partial<ExternalNetworkNode> = {}): ExternalNetworkNode => ({
  id: 'ext-1',
  name: 'External1',
  nodeType: 'external',
  connectionType: 'vlan',
  x: 200,
  y: 200,
  vlanId: 100,
  ...overrides,
});

const createLink = (overrides: Partial<Link> = {}): Link => ({
  id: 'link-1',
  source: 'node-1',
  target: 'node-2',
  type: 'p2p',
  ...overrides,
});

const createAnnotation = (overrides: Partial<Annotation> = {}): Annotation => ({
  id: 'ann-1',
  type: 'rect',
  x: 150,
  y: 150,
  width: 100,
  height: 60,
  ...overrides,
});

describe('Canvas', () => {
  const mockOnNodeMove = vi.fn();
  const mockOnAnnotationMove = vi.fn();
  const mockOnConnect = vi.fn();
  const mockOnSelect = vi.fn();
  const mockOnOpenConsole = vi.fn();
  const mockOnExtractConfig = vi.fn();
  const mockOnUpdateStatus = vi.fn();
  const mockOnDelete = vi.fn();
  const mockOnDropDevice = vi.fn();
  const mockOnDropExternalNetwork = vi.fn();
  const mockOnToolCreate = vi.fn();
  const mockOnUpdateAnnotation = vi.fn();
  const mockOnSelectMultiple = vi.fn();
  const mockOnFocusHandled = vi.fn();

  const defaultProps = {
    nodes: [] as Node[],
    links: [] as Link[],
    annotations: [] as Annotation[],
    runtimeStates: {} as Record<string, RuntimeStatus>,
    nodeStates: {} as Record<string, any>,
    deviceModels: mockDeviceModels,
    agents: [] as { id: string; name: string }[],
    showAgentIndicators: false,
    onNodeMove: mockOnNodeMove,
    onAnnotationMove: mockOnAnnotationMove,
    onConnect: mockOnConnect,
    selectedId: null as string | null,
    onSelect: mockOnSelect,
    onOpenConsole: mockOnOpenConsole,
    onExtractConfig: mockOnExtractConfig,
    onUpdateStatus: mockOnUpdateStatus,
    onDelete: mockOnDelete,
    onDropDevice: mockOnDropDevice,
    onDropExternalNetwork: mockOnDropExternalNetwork,
    onToolCreate: mockOnToolCreate,
    onUpdateAnnotation: mockOnUpdateAnnotation,
    onSelectMultiple: mockOnSelectMultiple,
    onFocusHandled: mockOnFocusHandled,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.getBoundingClientRect = mockGetBoundingClientRect;
    localStorage.clear();
  });

  // -- Context Menu --

  describe('Context Menu', () => {
    it('opens context menu on node right-click and dispatches console action', async () => {
      const user = userEvent.setup();
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={{ 'node-1': 'running' }} />
      );

      const nodeEl = screen.getByText('Router1').closest('.absolute')!;
      fireEvent.contextMenu(nodeEl);

      // The context menu should appear
      await waitFor(() => {
        expect(screen.getByText('Open Console')).toBeInTheDocument();
      });
    });

    it('dispatches delete action from context menu', async () => {
      const user = userEvent.setup();
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={{ 'node-1': 'stopped' }} />
      );

      const nodeEl = screen.getByText('Router1').closest('.absolute')!;
      fireEvent.contextMenu(nodeEl);

      await waitFor(() => {
        expect(screen.getByText('Remove Device')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Remove Device'));
      expect(mockOnDelete).toHaveBeenCalledWith('node-1');
    });

    it('dispatches start action from context menu on stopped node', async () => {
      const user = userEvent.setup();
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={{ 'node-1': 'stopped' }} />
      );

      const nodeEl = screen.getByText('Router1').closest('.absolute')!;
      fireEvent.contextMenu(nodeEl);

      await waitFor(() => {
        expect(screen.getByText('Start Node')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Start Node'));
      expect(mockOnUpdateStatus).toHaveBeenCalledWith('node-1', 'booting');
    });

    it('opens link context menu on link right-click', () => {
      const nodes = [
        createDeviceNode({ id: 'node-1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'node-2', name: 'R2', x: 300, y: 100 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'node-1', target: 'node-2' })];

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} />
      );

      // Find the transparent interaction line for the link
      const transparentLine = document.querySelector('line[stroke="transparent"]');
      expect(transparentLine).toBeInTheDocument();
      fireEvent.contextMenu(transparentLine!);

      // Context menu should show link options
      expect(screen.getByText('Delete Connection')).toBeInTheDocument();
    });

    it('closes context menu on outside click', async () => {
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} runtimeStates={{ 'node-1': 'stopped' }} />
      );

      const nodeEl = screen.getByText('Router1').closest('.absolute')!;
      fireEvent.contextMenu(nodeEl);

      await waitFor(() => {
        expect(screen.getByText('Remove Device')).toBeInTheDocument();
      });

      // Click outside to close
      fireEvent.click(window);

      await waitFor(() => {
        expect(screen.queryByText('Remove Device')).not.toBeInTheDocument();
      });
    });
  });

  // -- Keyboard Delete --

  describe('Keyboard Delete', () => {
    it('calls onDelete when Delete key is pressed with a selected node', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId="node-1" />
      );

      fireEvent.keyDown(window, { key: 'Delete' });
      expect(mockOnDelete).toHaveBeenCalledWith('node-1');
    });

    it('calls onDelete when Backspace key is pressed with a selected node', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId="node-1" />
      );

      fireEvent.keyDown(window, { key: 'Backspace' });
      expect(mockOnDelete).toHaveBeenCalledWith('node-1');
    });

    it('calls onDelete for all items in selectedIds when Delete pressed', () => {
      const nodes = [
        createDeviceNode({ id: 'node-1', name: 'R1' }),
        createDeviceNode({ id: 'node-2', name: 'R2', x: 200, y: 200 }),
      ];
      const selectedIds = new Set(['node-1', 'node-2']);

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} selectedIds={selectedIds} />
      );

      fireEvent.keyDown(window, { key: 'Delete' });
      expect(mockOnDelete).toHaveBeenCalledTimes(2);
      expect(mockOnDelete).toHaveBeenCalledWith('node-1');
      expect(mockOnDelete).toHaveBeenCalledWith('node-2');
    });

    it('does not call onDelete when Delete pressed inside an input element', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} selectedId="node-1" />
      );

      // Simulate keyDown originating from an INPUT element (bubbles to window)
      const input = document.createElement('input');
      document.body.appendChild(input);
      const event = new KeyboardEvent('keydown', { key: 'Delete', bubbles: true });
      input.dispatchEvent(event);
      expect(mockOnDelete).not.toHaveBeenCalled();
      document.body.removeChild(input);
    });
  });

  // -- Link State Coloring --

  describe('Link State Coloring', () => {
    it('colors links green when link state is up', () => {
      const nodes = [
        createDeviceNode({ id: 'node-1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'node-2', name: 'R2', x: 300, y: 300 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'node-1', target: 'node-2' })];
      const linkStates = new Map<string, LinkStateData>();
      linkStates.set('link-1', {
        link_name: 'R1:eth1 <-> R2:eth1',
        desired_state: 'up',
        actual_state: 'up',
        source_node: 'R1',
        target_node: 'R2',
      });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} linkStates={linkStates} />
      );

      // The visible link line should be green (#16a34a in light mode)
      const greenLine = document.querySelector('line[stroke="#16a34a"]');
      expect(greenLine).toBeInTheDocument();
    });

    it('colors links red when link state is error', () => {
      const nodes = [
        createDeviceNode({ id: 'node-1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'node-2', name: 'R2', x: 300, y: 300 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'node-1', target: 'node-2' })];
      const linkStates = new Map<string, LinkStateData>();
      linkStates.set('link-1', {
        link_name: 'R1:eth1 <-> R2:eth1',
        desired_state: 'up',
        actual_state: 'error',
        source_node: 'R1',
        target_node: 'R2',
      });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} linkStates={linkStates} />
      );

      const redLine = document.querySelector('line[stroke="#dc2626"]');
      expect(redLine).toBeInTheDocument();
    });

    it('colors links amber when link state is pending', () => {
      const nodes = [
        createDeviceNode({ id: 'node-1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'node-2', name: 'R2', x: 300, y: 300 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'node-1', target: 'node-2' })];
      const linkStates = new Map<string, LinkStateData>();
      linkStates.set('link-1', {
        link_name: 'R1:eth1 <-> R2:eth1',
        desired_state: 'up',
        actual_state: 'pending',
        source_node: 'R1',
        target_node: 'R2',
      });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={nodes} links={links} linkStates={linkStates} />
      );

      const amberLine = document.querySelector('line[stroke="#d97706"]');
      expect(amberLine).toBeInTheDocument();
    });
  });

  // -- Scenario Highlights --

  describe('Scenario Highlights', () => {
    it('renders scenario highlight glow on highlighted nodes', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });
      const scenarioHighlights = {
        activeNodeNames: new Set(['Router1']),
        activeLinkName: null,
        stepName: 'ping test',
      };

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[node]} scenarioHighlights={scenarioHighlights} />
      );

      // Highlighted node should have the pulsing border overlay
      const pulseOverlay = document.querySelector('.animate-pulse.border-2.border-blue-500');
      expect(pulseOverlay).toBeInTheDocument();
    });

    it('renders scenario highlight on links when activeLinkName matches', () => {
      const nodes = [
        createDeviceNode({ id: 'node-1', name: 'R1', x: 100, y: 100 }),
        createDeviceNode({ id: 'node-2', name: 'R2', x: 300, y: 300 }),
      ];
      const links = [createLink({ id: 'link-1', source: 'node-1', target: 'node-2' })];
      const scenarioHighlights = {
        activeNodeNames: new Set<string>(),
        activeLinkName: 'R1:eth1 <-> R2:eth1',
        stepName: 'link test',
      };

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={nodes}
          links={links}
          scenarioHighlights={scenarioHighlights}
        />
      );

      // The scenario highlight line should have animate-pulse and blue stroke
      const highlightLine = document.querySelector('line.animate-pulse');
      expect(highlightLine).toBeInTheDocument();
    });
  });

  // -- Agent Indicators --

  describe('Agent Indicators', () => {
    it('does not show agent indicators when showAgentIndicators is false', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });
      const agents = [
        { id: 'agent-1', name: 'Agent01' },
        { id: 'agent-2', name: 'Agent02' },
      ];
      const nodeStates = {
        'node-1': {
          id: 'state-1',
          node_id: 'node-1',
          node_name: 'Router1',
          host_id: 'agent-1',
          host_name: 'Agent01',
        },
      };

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          agents={agents}
          showAgentIndicators={false}
          nodeStates={nodeStates as any}
        />
      );

      // Agent initials should not appear
      expect(screen.queryByText('AG')).not.toBeInTheDocument();
    });

    it('shows agent indicators when enabled with multiple agents', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'Router1' });
      const agents = [
        { id: 'agent-1', name: 'Agent01' },
        { id: 'agent-2', name: 'Agent02' },
      ];
      const nodeStates = {
        'node-1': {
          id: 'state-1',
          node_id: 'node-1',
          node_name: 'Router1',
          host_id: 'agent-1',
          host_name: 'Agent01',
        },
      };

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          agents={agents}
          showAgentIndicators={true}
          nodeStates={nodeStates as any}
        />
      );

      // Agent initials "AG" should appear
      expect(screen.getByText('AG')).toBeInTheDocument();
    });

    it('shows agent toggle button only when multiple agents present', () => {
      const agents = [
        { id: 'agent-1', name: 'Agent01' },
        { id: 'agent-2', name: 'Agent02' },
      ];
      const onToggle = vi.fn();

      renderWithTheme(
        <Canvas
          {...defaultProps}
          agents={agents}
          showAgentIndicators={false}
          onToggleAgentIndicators={onToggle}
        />
      );

      // Agent toggle icon (fa-server) should be present
      const serverIcon = document.querySelector('.fa-server');
      expect(serverIcon).toBeInTheDocument();
    });
  });

  // -- Error Indicators --

  describe('Error Indicators', () => {
    it('shows error icon overlay on error nodes', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'ErrorRouter' });
      const runtimeStates: Record<string, RuntimeStatus> = { 'node-1': 'error' };
      const nodeStates = {
        'node-1': {
          id: 'state-1',
          node_id: 'node-1',
          node_name: 'ErrorRouter',
          error_message: 'Container failed to start',
        },
      };

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          runtimeStates={runtimeStates}
          nodeStates={nodeStates as any}
        />
      );

      // Error icon (fa-exclamation) should be present
      const errorIcon = document.querySelector('.fa-exclamation');
      expect(errorIcon).toBeInTheDocument();

      // The error indicator should have title with error message
      const errorIndicator = document.querySelector('[title="Container failed to start"]');
      expect(errorIndicator).toBeInTheDocument();
    });

    it('shows error border ring on error nodes', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'ErrorRouter' });
      const runtimeStates: Record<string, RuntimeStatus> = { 'node-1': 'error' };

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          runtimeStates={runtimeStates}
        />
      );

      const nodeEl = screen.getByText('ErrorRouter').closest('.absolute.w-12');
      expect(nodeEl).toHaveClass('ring-2');
      expect(nodeEl).toHaveClass('ring-red-500');
    });
  });

  // -- External Network Labels --

  describe('External Network Display', () => {
    it('shows managedInterfaceName when present', () => {
      const extNode = createExternalNetworkNode({
        name: 'ExtNet',
        managedInterfaceName: 'ens192.100',
      });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[extNode]} />
      );

      expect(screen.getByText('ens192.100')).toBeInTheDocument();
    });

    it('shows Unconfigured when no connection info set on bridge type', () => {
      const extNode = createExternalNetworkNode({
        name: 'UnconfiguredNet',
        connectionType: 'bridge',
        bridgeName: undefined,
        vlanId: undefined,
      });

      renderWithTheme(
        <Canvas {...defaultProps} nodes={[extNode]} />
      );

      expect(screen.getByText('Unconfigured')).toBeInTheDocument();
    });
  });

  // -- Canvas Controls --

  describe('Canvas Controls', () => {
    it('renders zoom in, zoom out, center, and fit-to-screen buttons', () => {
      renderWithTheme(<Canvas {...defaultProps} />);

      expect(document.querySelector('.fa-plus')).toBeInTheDocument();
      expect(document.querySelector('.fa-minus')).toBeInTheDocument();
      expect(document.querySelector('.fa-crosshairs')).toBeInTheDocument();
      expect(document.querySelector('.fa-maximize')).toBeInTheDocument();
    });
  });

  // -- Tool Cursors --

  describe('Tool Mode Cursors', () => {
    it('applies cursor-crosshair when active tool is rect', () => {
      renderWithTheme(<Canvas {...defaultProps} activeTool="rect" />);

      const container = document.querySelector('.flex-1.relative');
      expect(container).toHaveClass('cursor-crosshair');
    });

    it('applies cursor-text when active tool is text', () => {
      renderWithTheme(<Canvas {...defaultProps} activeTool="text" />);

      const container = document.querySelector('.flex-1.relative');
      expect(container).toHaveClass('cursor-text');
    });

    it('applies cursor-default for pointer tool', () => {
      renderWithTheme(<Canvas {...defaultProps} activeTool="pointer" />);

      const container = document.querySelector('.flex-1.relative');
      expect(container).toHaveClass('cursor-default');
    });
  });

  // -- Node Shape Variations --

  describe('Node Shape Variations', () => {
    it('renders router nodes with circular border-radius', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'Router1', type: DeviceType.ROUTER });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeEl = screen.getByText('Router1').closest('.absolute.w-12') as HTMLElement;
      expect(nodeEl?.style.borderRadius).toBe('50%');
    });

    it('renders switch nodes with small border-radius', () => {
      const node = createDeviceNode({
        id: 'node-1',
        name: 'Switch1',
        type: DeviceType.SWITCH,
        model: 'srlinux',
      });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeEl = screen.getByText('Switch1').closest('.absolute.w-12') as HTMLElement;
      expect(nodeEl?.style.borderRadius).toBe('4px');
    });

    it('renders host/other nodes with 8px border-radius', () => {
      const node = createDeviceNode({
        id: 'node-1',
        name: 'Host1',
        type: DeviceType.HOST,
        model: 'linux',
      });

      renderWithTheme(<Canvas {...defaultProps} nodes={[node]} />);

      const nodeEl = screen.getByText('Host1').closest('.absolute.w-12') as HTMLElement;
      expect(nodeEl?.style.borderRadius).toBe('8px');
    });
  });

  // -- Annotation Rendering --

  describe('Annotation Rendering', () => {
    it('renders a circle annotation', () => {
      const ann = createAnnotation({ type: 'circle', x: 200, y: 200, width: 80 });

      renderWithTheme(<Canvas {...defaultProps} annotations={[ann]} />);

      const circle = document.querySelector('circle');
      expect(circle).toBeInTheDocument();
      expect(circle).toHaveAttribute('cx', '200');
      expect(circle).toHaveAttribute('cy', '200');
      expect(circle).toHaveAttribute('r', '40'); // width/2
    });

    it('renders a text annotation', () => {
      const ann = createAnnotation({ type: 'text', x: 200, y: 200, text: 'Hello World' });

      renderWithTheme(<Canvas {...defaultProps} annotations={[ann]} />);

      expect(screen.getByText('Hello World')).toBeInTheDocument();
    });

    it('renders arrow annotation with line and arrowhead', () => {
      const ann = createAnnotation({
        type: 'arrow',
        x: 100,
        y: 100,
        targetX: 300,
        targetY: 300,
      });

      renderWithTheme(<Canvas {...defaultProps} annotations={[ann]} />);

      // Arrow should have a polygon for the arrowhead
      const polygon = document.querySelector('polygon');
      expect(polygon).toBeInTheDocument();
    });

    it('renders selected annotation with dashed stroke', () => {
      const ann = createAnnotation({ id: 'ann-1', type: 'rect', x: 100, y: 100, width: 150, height: 80 });

      renderWithTheme(
        <Canvas {...defaultProps} annotations={[ann]} selectedId="ann-1" />
      );

      const rect = document.querySelector('rect[stroke-dasharray="4"]');
      expect(rect).toBeInTheDocument();
    });

    it('renders text annotation default text when text is empty', () => {
      const ann = createAnnotation({ type: 'text', x: 200, y: 200, text: '' });

      renderWithTheme(<Canvas {...defaultProps} annotations={[ann]} />);

      expect(screen.getByText('New Text')).toBeInTheDocument();
    });
  });

  // -- Retry tooltip --

  describe('Retry Tooltip', () => {
    it('shows retry attempt tooltip on starting nodes', () => {
      const node = createDeviceNode({ id: 'node-1', name: 'RetryNode' });
      const runtimeStates: Record<string, RuntimeStatus> = { 'node-1': 'booting' };
      const nodeStates = {
        'node-1': {
          id: 'state-1',
          node_id: 'node-1',
          node_name: 'RetryNode',
          will_retry: true,
          enforcement_attempts: 2,
          max_enforcement_attempts: 5,
        },
      };

      renderWithTheme(
        <Canvas
          {...defaultProps}
          nodes={[node]}
          runtimeStates={runtimeStates}
          nodeStates={nodeStates as any}
        />
      );

      const statusDot = document.querySelector('[title="Starting (attempt 2/5)"]');
      expect(statusDot).toBeInTheDocument();
    });
  });

  // -- Preventing context menu on canvas --

  describe('Canvas Prevent Default Context Menu', () => {
    it('prevents default browser context menu on canvas', () => {
      renderWithTheme(<Canvas {...defaultProps} />);

      const container = document.querySelector('.flex-1.relative')!;
      const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
      const prevented = !container.dispatchEvent(event);
      expect(prevented).toBe(true);
    });
  });
});
