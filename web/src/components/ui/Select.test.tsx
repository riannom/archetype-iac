import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Select } from './Select';

describe('Select', () => {
  it('renders with options prop', () => {
    render(
      <Select
        options={[
          { value: 'a', label: 'Alpha' },
          { value: 'b', label: 'Beta' },
        ]}
      />,
    );
    expect(screen.getByRole('combobox')).toBeInTheDocument();
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });

  it('renders with children', () => {
    render(
      <Select>
        <option value="x">X-Ray</option>
      </Select>,
    );
    expect(screen.getByText('X-Ray')).toBeInTheDocument();
  });

  it('renders label', () => {
    render(<Select label="Color" options={[{ value: 'red', label: 'Red' }]} />);
    expect(screen.getByText('Color')).toBeInTheDocument();
  });

  it('renders error message', () => {
    render(<Select error="Required" options={[]} />);
    expect(screen.getByText('Required')).toBeInTheDocument();
  });

  it('renders hint when no error', () => {
    render(<Select hint="Pick one" options={[]} />);
    expect(screen.getByText('Pick one')).toBeInTheDocument();
  });

  it('calls onChange', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Select
        onChange={onChange}
        options={[
          { value: 'a', label: 'Alpha' },
          { value: 'b', label: 'Beta' },
        ]}
      />,
    );
    await user.selectOptions(screen.getByRole('combobox'), 'b');
    expect(onChange).toHaveBeenCalled();
  });

  it('applies disabled state', () => {
    render(<Select disabled options={[{ value: 'a', label: 'A' }]} />);
    expect(screen.getByRole('combobox')).toBeDisabled();
  });

  it('applies custom className', () => {
    render(<Select className="custom" options={[]} />);
    expect(screen.getByRole('combobox')).toHaveClass('custom');
  });
});
