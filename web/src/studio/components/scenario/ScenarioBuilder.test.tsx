import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ScenarioBuilder from './ScenarioBuilder';
import type { ScenarioFormState } from './scenarioTypes';

const emptyState: ScenarioFormState = {
  name: 'Test Scenario',
  description: '',
  steps: [],
};

const defaultProps = {
  state: emptyState,
  onChange: vi.fn(),
  nodes: [
    { id: 'n1', name: 'router1', nodeType: 'device' as const, type: 'router' as const, model: 'ceos', version: '4.30', x: 0, y: 0 },
    { id: 'n2', name: 'router2', nodeType: 'device' as const, type: 'router' as const, model: 'ceos', version: '4.30', x: 100, y: 0 },
  ],
  links: [
    { id: 'l1', source: 'n1', target: 'n2', type: 'p2p' as const, sourceInterface: 'eth1', targetInterface: 'eth1' },
  ],
};

describe('ScenarioBuilder', () => {
  it('renders empty state message when no steps', () => {
    render(<ScenarioBuilder {...defaultProps} />);
    expect(screen.getByText('Add steps using the buttons above')).toBeInTheDocument();
  });

  it('renders template buttons for all step types', () => {
    render(<ScenarioBuilder {...defaultProps} />);
    expect(screen.getByText('+Verify')).toBeInTheDocument();
    expect(screen.getByText('+Link Down')).toBeInTheDocument();
    expect(screen.getByText('+Link Up')).toBeInTheDocument();
    expect(screen.getByText('+Stop')).toBeInTheDocument();
    expect(screen.getByText('+Start')).toBeInTheDocument();
    expect(screen.getByText('+Wait')).toBeInTheDocument();
    expect(screen.getByText('+Exec')).toBeInTheDocument();
  });

  it('calls onChange with a new step when template button clicked', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ScenarioBuilder {...defaultProps} onChange={onChange} />);

    await user.click(screen.getByText('+Wait'));

    expect(onChange).toHaveBeenCalledTimes(1);
    const newState = onChange.mock.calls[0][0] as ScenarioFormState;
    expect(newState.steps).toHaveLength(1);
    expect(newState.steps[0].type).toBe('wait');
  });

  it('pre-fills node on exec step', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ScenarioBuilder {...defaultProps} onChange={onChange} />);

    await user.click(screen.getByText('+Exec'));

    const newState = onChange.mock.calls[0][0] as ScenarioFormState;
    expect(newState.steps[0].type).toBe('exec');
    if (newState.steps[0].type === 'exec') {
      expect(newState.steps[0].node).toBe('router1');
    }
  });

  it('pre-fills link on link_down step', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ScenarioBuilder {...defaultProps} onChange={onChange} />);

    await user.click(screen.getByText('+Link Down'));

    const newState = onChange.mock.calls[0][0] as ScenarioFormState;
    if (newState.steps[0].type === 'link_down') {
      expect(newState.steps[0].link).toContain('<->');
    }
  });

  it('renders step cards for existing steps', () => {
    const stateWithSteps: ScenarioFormState = {
      ...emptyState,
      steps: [
        { id: 'step-1', type: 'wait', name: 'Pause', seconds: 5 },
        { id: 'step-2', type: 'exec', name: 'Check', node: 'r1', cmd: 'show ver' },
      ],
    };
    render(<ScenarioBuilder {...defaultProps} state={stateWithSteps} />);
    expect(screen.getByTestId('step-card-0')).toBeInTheDocument();
    expect(screen.getByTestId('step-card-1')).toBeInTheDocument();
  });

  it('calls onChange to remove a step', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    const stateWithSteps: ScenarioFormState = {
      ...emptyState,
      steps: [{ id: 'step-1', type: 'wait', name: 'Pause', seconds: 5 }],
    };
    render(<ScenarioBuilder {...defaultProps} state={stateWithSteps} onChange={onChange} />);

    await user.click(screen.getByTitle('Remove step'));

    expect(onChange).toHaveBeenCalledTimes(1);
    const newState = onChange.mock.calls[0][0] as ScenarioFormState;
    expect(newState.steps).toHaveLength(0);
  });
});
