import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import NodeStepFields from './NodeStepFields';
import type { NodeStep } from '../scenarioTypes';
import type { Node } from '../../../types';
import { DeviceType } from '../../../types';

const makeNode = (id: string, name: string): Node => ({
  id,
  name,
  x: 0,
  y: 0,
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'ceos',
  version: 'latest',
});

const baseStep: NodeStep = {
  id: 'step-1',
  type: 'node_start',
  name: 'Start router',
  node: '',
};

describe('NodeStepFields', () => {
  it('renders node selector with placeholder and node options', () => {
    const nodes = [makeNode('n1', 'r1'), makeNode('n2', 'r2')];
    render(<NodeStepFields step={baseStep} nodes={nodes} onUpdate={vi.fn()} />);

    expect(screen.getByText('Select node...')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'r1' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'r2' })).toBeInTheDocument();
  });

  it('reflects the selected node value', () => {
    const nodes = [makeNode('n1', 'r1')];
    render(
      <NodeStepFields step={{ ...baseStep, node: 'r1' }} nodes={nodes} onUpdate={vi.fn()} />,
    );
    const selects = screen.getAllByRole('combobox');
    expect(selects[0]).toHaveValue('r1');
  });

  it('calls onUpdate when a node is selected', async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    const nodes = [makeNode('n1', 'r1')];
    render(<NodeStepFields step={baseStep} nodes={nodes} onUpdate={onUpdate} />);

    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[0], 'r1');
    expect(onUpdate).toHaveBeenCalledWith({ node: 'r1' });
  });

  it('defaults timeout display to 120 for node_start', () => {
    render(<NodeStepFields step={baseStep} nodes={[]} onUpdate={vi.fn()} />);
    expect(screen.getByRole('spinbutton')).toHaveValue(120);
  });

  it('defaults timeout display to 60 for node_stop', () => {
    render(
      <NodeStepFields
        step={{ ...baseStep, type: 'node_stop' }}
        nodes={[]}
        onUpdate={vi.fn()}
      />,
    );
    expect(screen.getByRole('spinbutton')).toHaveValue(60);
  });

  it('uses explicit timeout value when set', () => {
    render(
      <NodeStepFields step={{ ...baseStep, timeout: 42 }} nodes={[]} onUpdate={vi.fn()} />,
    );
    expect(screen.getByRole('spinbutton')).toHaveValue(42);
  });

  it('emits parsed integer when timeout input changes', () => {
    const onUpdate = vi.fn();
    render(<NodeStepFields step={baseStep} nodes={[]} onUpdate={onUpdate} />);

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '90' } });
    expect(onUpdate).toHaveBeenCalledWith({ timeout: 90 });
  });

  it('emits undefined timeout when cleared', () => {
    const onUpdate = vi.fn();
    render(
      <NodeStepFields step={{ ...baseStep, timeout: 30 }} nodes={[]} onUpdate={onUpdate} />,
    );

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '' } });
    expect(onUpdate).toHaveBeenCalledWith({ timeout: undefined });
  });

  it('emits undefined timeout when value is below 1', () => {
    const onUpdate = vi.fn();
    render(<NodeStepFields step={baseStep} nodes={[]} onUpdate={onUpdate} />);

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '0' } });
    expect(onUpdate).toHaveBeenCalledWith({ timeout: undefined });
  });

  it('disables both inputs when disabled prop is set', () => {
    render(<NodeStepFields step={baseStep} nodes={[]} disabled onUpdate={vi.fn()} />);
    for (const el of screen.getAllByRole('combobox')) {
      expect(el).toBeDisabled();
    }
    expect(screen.getByRole('spinbutton')).toBeDisabled();
  });
});
