import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StepCard from './StepCard';
import type { ScenarioStep } from './scenarioTypes';

const defaultProps = {
  step: { id: 'step-test-1', type: 'wait' as const, name: 'Pause', seconds: 5 },
  index: 0,
  total: 2,
  nodes: [],
  links: [],
  linkOptions: [],
  onUpdate: vi.fn(),
  onRemove: vi.fn(),
  onMove: vi.fn(),
};

describe('StepCard', () => {
  it('renders step type badge', () => {
    render(<StepCard {...defaultProps} />);
    const badges = screen.getAllByText('Wait');
    // Badge in header + option in type selector
    expect(badges.length).toBeGreaterThanOrEqual(1);
    // The badge has the uppercase styling class
    expect(badges[0].className).toContain('font-black');
  });

  it('renders step name input with current value', () => {
    render(<StepCard {...defaultProps} />);
    const input = screen.getByPlaceholderText('Step name...');
    expect(input).toHaveValue('Pause');
  });

  it('calls onUpdate when name is changed', async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    render(<StepCard {...defaultProps} onUpdate={onUpdate} />);

    const input = screen.getByPlaceholderText('Step name...');
    await user.clear(input);
    await user.type(input, 'New Name');

    expect(onUpdate).toHaveBeenCalled();
  });

  it('calls onRemove when delete button clicked', async () => {
    const onRemove = vi.fn();
    const user = userEvent.setup();
    render(<StepCard {...defaultProps} onRemove={onRemove} />);

    await user.click(screen.getByTitle('Remove step'));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it('calls onMove(-1) when up button clicked', async () => {
    const onMove = vi.fn();
    const user = userEvent.setup();
    render(<StepCard {...defaultProps} index={1} onMove={onMove} />);

    await user.click(screen.getByTitle('Move up'));
    expect(onMove).toHaveBeenCalledWith(-1);
  });

  it('disables up button for first step', () => {
    render(<StepCard {...defaultProps} index={0} />);
    expect(screen.getByTitle('Move up')).toBeDisabled();
  });

  it('disables down button for last step', () => {
    render(<StepCard {...defaultProps} index={1} total={2} />);
    expect(screen.getByTitle('Move down')).toBeDisabled();
  });

  it('changes step type when type selector is changed', async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    render(<StepCard {...defaultProps} onUpdate={onUpdate} />);

    const typeSelect = screen.getByDisplayValue('Wait');
    await user.selectOptions(typeSelect, 'exec');

    expect(onUpdate).toHaveBeenCalled();
    const updatedStep = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0] as ScenarioStep;
    expect(updatedStep.type).toBe('exec');
  });

  it('collapses and expands the card body', async () => {
    const user = userEvent.setup();
    render(<StepCard {...defaultProps} />);

    // Body should be visible initially
    expect(screen.getByText('Seconds')).toBeInTheDocument();

    // Collapse
    await user.click(screen.getByTitle('Collapse'));
    expect(screen.queryByText('Seconds')).not.toBeInTheDocument();

    // Expand
    await user.click(screen.getByTitle('Expand'));
    expect(screen.getByText('Seconds')).toBeInTheDocument();
  });
});
