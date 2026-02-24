import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import TestBuilder from './TestBuilder';
import { TestSpec, Node, Link, DeviceNode } from '../types';

const makeNode = (id: string, name: string): DeviceNode => ({
  id,
  name,
  nodeType: 'device',
  type: 'router',
  model: 'ceos',
  version: 'latest',
  x: 0,
  y: 0,
});

const makeLink = (id: string, source: string, target: string, srcIf: string, tgtIf: string): Link => ({
  id,
  source,
  target,
  type: 'p2p',
  sourceInterface: srcIf,
  targetInterface: tgtIf,
});

describe('TestBuilder', () => {
  const nodes: Node[] = [makeNode('n1', 'r1'), makeNode('n2', 'r2')];
  const links: Link[] = [makeLink('l1', 'n1', 'n2', 'eth1', 'eth1')];
  let onUpdateSpecs: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    onUpdateSpecs = vi.fn();
  });

  it('renders empty state when no specs', () => {
    render(<TestBuilder specs={[]} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    expect(screen.getByText('Add tests using the buttons above')).toBeTruthy();
  });

  it('renders all four template buttons', () => {
    render(<TestBuilder specs={[]} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    expect(screen.getByText('+Ping')).toBeTruthy();
    expect(screen.getByText('+Link')).toBeTruthy();
    expect(screen.getByText('+Node')).toBeTruthy();
    expect(screen.getByText('+Command')).toBeTruthy();
  });

  it('adds a ping spec when +Ping is clicked', () => {
    render(<TestBuilder specs={[]} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    fireEvent.click(screen.getByText('+Ping'));
    expect(onUpdateSpecs).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'ping', source: 'r1', count: 3 }),
    ]);
  });

  it('adds a link_state spec when +Link is clicked', () => {
    render(<TestBuilder specs={[]} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    fireEvent.click(screen.getByText('+Link'));
    expect(onUpdateSpecs).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'link_state', expected_state: 'up' }),
    ]);
  });

  it('adds a node_state spec when +Node is clicked', () => {
    render(<TestBuilder specs={[]} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    fireEvent.click(screen.getByText('+Node'));
    expect(onUpdateSpecs).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'node_state', node_name: 'r1', expected_state: 'running' }),
    ]);
  });

  it('adds a command spec when +Command is clicked', () => {
    render(<TestBuilder specs={[]} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    fireEvent.click(screen.getByText('+Command'));
    expect(onUpdateSpecs).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'command', node: 'r1' }),
    ]);
  });

  it('renders a spec card for each spec', () => {
    const specs: TestSpec[] = [
      { type: 'ping', source: 'r1', target: '10.0.0.1', count: 3 },
      { type: 'node_state', node_name: 'r2', expected_state: 'running' },
    ];
    render(<TestBuilder specs={specs} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    expect(screen.getByTestId('spec-card-0')).toBeTruthy();
    expect(screen.getByTestId('spec-card-1')).toBeTruthy();
  });

  it('removes a spec when delete is clicked', () => {
    const specs: TestSpec[] = [
      { type: 'ping', source: 'r1', target: '10.0.0.1', count: 3 },
      { type: 'node_state', node_name: 'r2', expected_state: 'running' },
    ];
    render(<TestBuilder specs={specs} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    // Click first card's remove button
    const removeButtons = screen.getAllByTitle('Remove test');
    fireEvent.click(removeButtons[0]);
    expect(onUpdateSpecs).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'node_state' }),
    ]);
  });

  it('moves a spec up when up arrow is clicked', () => {
    const specs: TestSpec[] = [
      { type: 'ping', source: 'r1', target: '10.0.0.1', count: 3 },
      { type: 'node_state', node_name: 'r2', expected_state: 'running' },
    ];
    render(<TestBuilder specs={specs} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    // Click second card's move-up button
    const upButtons = screen.getAllByTitle('Move up');
    fireEvent.click(upButtons[1]);
    expect(onUpdateSpecs).toHaveBeenCalledWith([
      expect.objectContaining({ type: 'node_state' }),
      expect.objectContaining({ type: 'ping' }),
    ]);
  });

  it('disables all controls when disabled prop is true', () => {
    const specs: TestSpec[] = [{ type: 'ping', source: 'r1', target: '10.0.0.1', count: 3 }];
    render(<TestBuilder specs={specs} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} disabled />);
    // Template buttons should be disabled
    expect((screen.getByText('+Ping') as HTMLButtonElement).closest('button')!.disabled).toBe(true);
    // Remove button should be disabled
    expect((screen.getByTitle('Remove test') as HTMLButtonElement).disabled).toBe(true);
  });

  it('updates ping target when input changes', () => {
    const specs: TestSpec[] = [{ type: 'ping', source: 'r1', target: '', count: 3 }];
    render(<TestBuilder specs={specs} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    const input = screen.getByPlaceholderText('IP address or hostname');
    fireEvent.change(input, { target: { value: '192.168.1.1' } });
    expect(onUpdateSpecs).toHaveBeenCalledWith([
      expect.objectContaining({ target: '192.168.1.1' }),
    ]);
  });

  it('updates command text when input changes', () => {
    const specs: TestSpec[] = [{ type: 'command', node: 'r1', cmd: '' }];
    render(<TestBuilder specs={specs} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    const input = screen.getByPlaceholderText('show version');
    fireEvent.change(input, { target: { value: 'show ip route' } });
    expect(onUpdateSpecs).toHaveBeenCalledWith([
      expect.objectContaining({ cmd: 'show ip route' }),
    ]);
  });

  it('builds link names from nodes and links arrays', () => {
    const specs: TestSpec[] = [{ type: 'link_state', expected_state: 'up' }];
    render(<TestBuilder specs={specs} onUpdateSpecs={onUpdateSpecs} nodes={nodes} links={links} />);
    // The select should have the constructed link name as an option
    const options = screen.getAllByRole('option');
    const linkOption = options.find(o => (o as HTMLOptionElement).value.includes('r1:eth1'));
    expect(linkOption).toBeTruthy();
  });
});
