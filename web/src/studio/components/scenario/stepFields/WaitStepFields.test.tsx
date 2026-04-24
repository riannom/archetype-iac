import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import WaitStepFields from './WaitStepFields';
import type { WaitStep } from '../scenarioTypes';

const baseStep: WaitStep = {
  id: 'step-1',
  type: 'wait',
  name: 'Pause',
  seconds: 5,
};

describe('WaitStepFields', () => {
  it('renders the seconds input with the provided value', () => {
    render(<WaitStepFields step={baseStep} onUpdate={vi.fn()} />);
    expect(screen.getByRole('spinbutton')).toHaveValue(5);
  });

  it('emits parsed integer when seconds change', () => {
    const onUpdate = vi.fn();
    render(<WaitStepFields step={baseStep} onUpdate={onUpdate} />);

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '12' } });
    expect(onUpdate).toHaveBeenCalledWith({ seconds: 12 });
  });

  it('falls back to 1 when input cannot be parsed (e.g. cleared)', () => {
    const onUpdate = vi.fn();
    render(<WaitStepFields step={baseStep} onUpdate={onUpdate} />);

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '' } });
    expect(onUpdate).toHaveBeenCalledWith({ seconds: 1 });
  });

  it('disables the input when disabled prop is set', () => {
    render(<WaitStepFields step={baseStep} disabled onUpdate={vi.fn()} />);
    expect(screen.getByRole('spinbutton')).toBeDisabled();
  });
});
