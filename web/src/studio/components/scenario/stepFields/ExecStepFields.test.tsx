import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ExecStepFields from './ExecStepFields';
import type { ExecStep } from '../scenarioTypes';
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

const baseStep: ExecStep = {
  id: 'step-1',
  type: 'exec',
  name: 'Run command',
  node: '',
  cmd: '',
};

describe('ExecStepFields', () => {
  it('renders node options, command field, and expect field', () => {
    const nodes = [makeNode('n1', 'r1'), makeNode('n2', 'r2')];
    render(<ExecStepFields step={baseStep} nodes={nodes} onUpdate={vi.fn()} />);

    expect(screen.getByText('Select node...')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'r1' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('show version')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Regex pattern (optional)')).toBeInTheDocument();
  });

  it('emits node patch when node is selected', async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    const nodes = [makeNode('n1', 'r1')];
    render(<ExecStepFields step={baseStep} nodes={nodes} onUpdate={onUpdate} />);

    await user.selectOptions(screen.getByRole('combobox'), 'r1');
    expect(onUpdate).toHaveBeenCalledWith({ node: 'r1' });
  });

  it('emits cmd patch when command is typed', async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    render(<ExecStepFields step={baseStep} nodes={[]} onUpdate={onUpdate} />);

    await user.type(screen.getByPlaceholderText('show version'), 'a');
    expect(onUpdate).toHaveBeenCalledWith({ cmd: 'a' });
  });

  it('emits expect patch when text is entered', async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    render(<ExecStepFields step={baseStep} nodes={[]} onUpdate={onUpdate} />);

    await user.type(screen.getByPlaceholderText('Regex pattern (optional)'), 'x');
    expect(onUpdate).toHaveBeenCalledWith({ expect: 'x' });
  });

  it('emits expect: undefined when expect is cleared', async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    render(
      <ExecStepFields
        step={{ ...baseStep, expect: 'matched' }}
        nodes={[]}
        onUpdate={onUpdate}
      />,
    );

    await user.clear(screen.getByPlaceholderText('Regex pattern (optional)'));
    expect(onUpdate).toHaveBeenCalledWith({ expect: undefined });
  });

  it('renders empty string for expect when value is undefined', () => {
    render(<ExecStepFields step={baseStep} nodes={[]} onUpdate={vi.fn()} />);
    expect(screen.getByPlaceholderText('Regex pattern (optional)')).toHaveValue('');
  });

  it('disables every input when disabled prop is set', () => {
    render(<ExecStepFields step={baseStep} nodes={[]} disabled onUpdate={vi.fn()} />);
    expect(screen.getByRole('combobox')).toBeDisabled();
    expect(screen.getByPlaceholderText('show version')).toBeDisabled();
    expect(screen.getByPlaceholderText('Regex pattern (optional)')).toBeDisabled();
  });
});
