import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ContextMenuOverlay } from './ContextMenuOverlay';
import type { ContextMenu } from './types';
import type { Node, DeviceNode, ExternalNetworkNode } from '../../types';
import type { RuntimeStatus } from '../RuntimeControl';
import { DeviceType } from '../../types';

function makeDeviceNode(overrides: Partial<DeviceNode> = {}): DeviceNode {
  return {
    id: 'node-1',
    name: 'Router1',
    x: 100,
    y: 200,
    nodeType: 'device',
    type: DeviceType.ROUTER,
    model: 'ceos',
    version: '4.28.0F',
    ...overrides,
  };
}

function makeExternalNode(overrides: Partial<ExternalNetworkNode> = {}): ExternalNetworkNode {
  return {
    id: 'ext-1',
    name: 'External-Net',
    x: 300,
    y: 100,
    nodeType: 'external',
    ...overrides,
  };
}

describe('ContextMenuOverlay', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Node Context Menu ──

  it('renders at the specified position', () => {
    const contextMenu: ContextMenu = { x: 150, y: 250, id: 'node-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['node-1', makeDeviceNode()]]);
    const { container } = render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={vi.fn()}
      />
    );
    const menu = container.firstChild as HTMLElement;
    expect(menu.style.left).toBe('150px');
    expect(menu.style.top).toBe('250px');
  });

  it('renders "Node Actions" header for device nodes', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'node-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['node-1', makeDeviceNode()]]);
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Node Actions')).toBeInTheDocument();
  });

  it('renders console, extract config, and start actions for stopped device node', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'node-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['node-1', makeDeviceNode()]]);
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{ 'node-1': 'stopped' }}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Open Console')).toBeInTheDocument();
    expect(screen.getByText('Extract Config')).toBeInTheDocument();
    expect(screen.getByText('Start Node')).toBeInTheDocument();
    expect(screen.queryByText('Stop Node')).not.toBeInTheDocument();
  });

  it('renders stop action instead of start when node is running', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'node-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['node-1', makeDeviceNode()]]);
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{ 'node-1': 'running' }}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Stop Node')).toBeInTheDocument();
    expect(screen.queryByText('Start Node')).not.toBeInTheDocument();
  });

  it('renders stop action when node is booting', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'node-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['node-1', makeDeviceNode()]]);
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{ 'node-1': 'booting' }}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Stop Node')).toBeInTheDocument();
    expect(screen.queryByText('Start Node')).not.toBeInTheDocument();
  });

  it('dispatches correct action when menu items are clicked', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'node-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['node-1', makeDeviceNode()]]);
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{ 'node-1': 'stopped' }}
        onAction={onAction}
      />
    );
    await user.click(screen.getByText('Open Console'));
    expect(onAction).toHaveBeenCalledWith('console');

    await user.click(screen.getByText('Extract Config'));
    expect(onAction).toHaveBeenCalledWith('extract-config');

    await user.click(screen.getByText('Start Node'));
    expect(onAction).toHaveBeenCalledWith('start');
  });

  it('dispatches delete action for Remove Device', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'node-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['node-1', makeDeviceNode()]]);
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={onAction}
      />
    );
    await user.click(screen.getByText('Remove Device'));
    expect(onAction).toHaveBeenCalledWith('delete');
  });

  // ── External Network Node ──

  it('renders "External Network" header for external nodes', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'ext-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['ext-1', makeExternalNode()]]);
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('External Network')).toBeInTheDocument();
  });

  it('only shows "Remove External Network" for external nodes (no console/start/stop)', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'ext-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['ext-1', makeExternalNode()]]);
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Remove External Network')).toBeInTheDocument();
    expect(screen.queryByText('Open Console')).not.toBeInTheDocument();
    expect(screen.queryByText('Start Node')).not.toBeInTheDocument();
    expect(screen.queryByText('Stop Node')).not.toBeInTheDocument();
  });

  // ── Link Context Menu ──

  it('renders "Link Actions" header for link context menu', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'link-1', type: 'link' };
    const nodeMap = new Map<string, Node>();
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Link Actions')).toBeInTheDocument();
  });

  it('renders "Delete Connection" for link context menu', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'link-1', type: 'link' };
    const nodeMap = new Map<string, Node>();
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={vi.fn()}
      />
    );
    expect(screen.getByText('Delete Connection')).toBeInTheDocument();
  });

  it('dispatches delete action for Delete Connection', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'link-1', type: 'link' };
    const nodeMap = new Map<string, Node>();
    render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={onAction}
      />
    );
    await user.click(screen.getByText('Delete Connection'));
    expect(onAction).toHaveBeenCalledWith('delete');
  });

  // ── Mouse event propagation ──

  it('stops mousedown propagation on the menu container', () => {
    const contextMenu: ContextMenu = { x: 0, y: 0, id: 'node-1', type: 'node' };
    const nodeMap = new Map<string, Node>([['node-1', makeDeviceNode()]]);
    const { container } = render(
      <ContextMenuOverlay
        contextMenu={contextMenu}
        nodeMap={nodeMap}
        runtimeStates={{}}
        onAction={vi.fn()}
      />
    );
    const menu = container.firstChild as HTMLElement;
    const event = new MouseEvent('mousedown', { bubbles: true });
    const stopPropagation = vi.spyOn(event, 'stopPropagation');
    menu.dispatchEvent(event);
    expect(stopPropagation).toHaveBeenCalled();
  });
});
