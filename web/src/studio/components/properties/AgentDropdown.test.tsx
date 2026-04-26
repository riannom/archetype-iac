import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AgentDropdown from './AgentDropdown';

const agents = [
  { id: 'agent-1', name: 'Agent One' },
  { id: 'agent-2', name: 'Agent Two' },
];

describe('AgentDropdown', () => {
  it('shows the auto placeholder when value is empty', () => {
    render(<AgentDropdown value="" onChange={vi.fn()} agents={agents} />);
    expect(screen.getByText('Auto (any available agent)')).toBeInTheDocument();
  });

  it('shows the selected agent name when value matches an agent id', () => {
    render(<AgentDropdown value="agent-2" onChange={vi.fn()} agents={agents} />);
    expect(screen.getByText('Agent Two')).toBeInTheDocument();
  });

  it('opens the option list when the trigger is clicked', async () => {
    const user = userEvent.setup();
    render(<AgentDropdown value="" onChange={vi.fn()} agents={agents} />);

    expect(screen.queryByText('Agent One')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button'));
    expect(screen.getByText('Agent One')).toBeInTheDocument();
    expect(screen.getByText('Agent Two')).toBeInTheDocument();
  });

  it('calls onChange and closes when an agent is picked', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<AgentDropdown value="" onChange={onChange} agents={agents} />);

    await user.click(screen.getByRole('button'));
    await user.click(screen.getByText('Agent One'));
    expect(onChange).toHaveBeenCalledWith('agent-1');
    // Option list should be closed (only the trigger remains)
    expect(screen.queryByText('Agent Two')).not.toBeInTheDocument();
  });

  it('emits empty string when the Auto option is picked', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<AgentDropdown value="agent-1" onChange={onChange} agents={agents} />);

    await user.click(screen.getByRole('button'));
    // Two "Auto" entries appear when open: one option, one trigger label
    const autos = screen.getAllByText('Auto (any available agent)');
    await user.click(autos[autos.length - 1]);
    expect(onChange).toHaveBeenCalledWith('');
  });

  it('does not toggle and shows disabled hint when disabled', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <AgentDropdown value="" onChange={onChange} agents={agents} disabled />,
    );

    expect(screen.getByText('Stop node to change agent placement')).toBeInTheDocument();
    await user.click(screen.getByRole('button'));
    expect(screen.queryByText('Agent One')).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('shows the enabled hint when not disabled', () => {
    render(<AgentDropdown value="" onChange={vi.fn()} agents={agents} />);
    expect(screen.getByText('Select which agent runs this node')).toBeInTheDocument();
  });

  it('closes when a click happens outside the dropdown', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button data-testid="outside">outside</button>
        <AgentDropdown value="" onChange={vi.fn()} agents={agents} />
      </div>,
    );

    await user.click(screen.getByRole('button', { name: /Auto/ }));
    expect(screen.getByText('Agent One')).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId('outside'));
    expect(screen.queryByText('Agent One')).not.toBeInTheDocument();
  });

  it('keeps the dropdown open when clicking inside it', async () => {
    const user = userEvent.setup();
    render(<AgentDropdown value="" onChange={vi.fn()} agents={agents} />);

    await user.click(screen.getByRole('button'));
    expect(screen.getByText('Agent One')).toBeInTheDocument();

    // Click inside the dropdown container (e.g., the auto option label area)
    fireEvent.mouseDown(screen.getByText('Agent One'));
    // Dropdown remains open (clicking the option's label as a child)
    expect(screen.getByText('Agent One')).toBeInTheDocument();
  });
});
