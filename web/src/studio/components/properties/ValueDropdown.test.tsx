import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ValueDropdown from './ValueDropdown';

const options = ['x86_64', 'arm64', 'i386'];

describe('ValueDropdown', () => {
  it('renders the label and current value', () => {
    render(
      <ValueDropdown label="Architecture" value="arm64" options={options} onChange={vi.fn()} />,
    );
    expect(screen.getByText('Architecture')).toBeInTheDocument();
    expect(screen.getByText('arm64')).toBeInTheDocument();
  });

  it('opens the option list when the trigger is clicked', async () => {
    const user = userEvent.setup();
    render(
      <ValueDropdown label="Arch" value="" options={options} onChange={vi.fn()} />,
    );

    expect(screen.queryByText('x86_64')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button'));
    for (const opt of options) {
      expect(screen.getByText(opt)).toBeInTheDocument();
    }
  });

  it('calls onChange and closes when an option is picked', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ValueDropdown label="Arch" value="x86_64" options={options} onChange={onChange} />,
    );

    await user.click(screen.getByRole('button'));
    await user.click(screen.getByText('arm64'));
    expect(onChange).toHaveBeenCalledWith('arm64');
    expect(screen.queryByText('i386')).not.toBeInTheDocument();
  });

  it('does not toggle when disabled', async () => {
    const user = userEvent.setup();
    render(
      <ValueDropdown
        label="Arch"
        value="arm64"
        options={options}
        onChange={vi.fn()}
        disabled
      />,
    );

    await user.click(screen.getByRole('button'));
    expect(screen.queryByText('x86_64')).not.toBeInTheDocument();
  });

  it('closes when a click happens outside', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button data-testid="outside">outside</button>
        <ValueDropdown label="Arch" value="" options={options} onChange={vi.fn()} />
      </div>,
    );

    await user.click(screen.getByRole('button', { name: '' })); // dropdown trigger has no name when value is empty
    expect(screen.getByText('x86_64')).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId('outside'));
    expect(screen.queryByText('x86_64')).not.toBeInTheDocument();
  });

  it('toggles closed when the trigger is clicked while open', async () => {
    const user = userEvent.setup();
    render(
      <ValueDropdown label="Arch" value="x86_64" options={options} onChange={vi.fn()} />,
    );

    const trigger = screen.getByRole('button');
    await user.click(trigger);
    expect(screen.getByText('arm64')).toBeInTheDocument();
    await user.click(trigger);
    expect(screen.queryByText('arm64')).not.toBeInTheDocument();
  });
});
