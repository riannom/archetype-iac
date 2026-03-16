import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Tooltip } from './Tooltip';

describe('Tooltip', () => {
  it('renders children', () => {
    render(
      <Tooltip content="Help text">
        <button>Hover me</button>
      </Tooltip>,
    );
    expect(screen.getByRole('button', { name: 'Hover me' })).toBeInTheDocument();
  });

  it('does not show tooltip initially', () => {
    render(
      <Tooltip content="Help text">
        <button>Hover me</button>
      </Tooltip>,
    );
    expect(screen.queryByText('Help text')).not.toBeInTheDocument();
  });

  it('shows tooltip on hover after delay', async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(
      <Tooltip content="Help text" delay={100}>
        <button>Hover me</button>
      </Tooltip>,
    );

    await user.hover(screen.getByRole('button'));
    act(() => { vi.advanceTimersByTime(150); });

    expect(screen.getByText('Help text')).toBeInTheDocument();

    vi.useRealTimers();
  });

  it('clears timeout on mouse leave before showing', async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(
      <Tooltip content="Help text" delay={500}>
        <button>Hover me</button>
      </Tooltip>,
    );

    // Hover then immediately leave before delay expires
    await user.hover(screen.getByRole('button'));
    await user.unhover(screen.getByRole('button'));
    act(() => { vi.advanceTimersByTime(600); });

    // Should NOT show because we left before delay
    expect(screen.queryByText('Help text')).not.toBeInTheDocument();

    vi.useRealTimers();
  });
});
