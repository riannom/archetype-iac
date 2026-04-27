import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, act } from '@testing-library/react';
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

  it('shows tooltip on focus and hides on blur', () => {
    vi.useFakeTimers();
    render(
      <Tooltip content="Help text" delay={50}>
        <button>Focus me</button>
      </Tooltip>,
    );

    const btn = screen.getByRole('button');
    act(() => {
      btn.focus();
      vi.advanceTimersByTime(100);
    });
    expect(screen.getByText('Help text')).toBeInTheDocument();

    act(() => {
      btn.blur();
    });
    expect(screen.queryByText('Help text')).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  it.each(['top', 'bottom', 'left', 'right'] as const)(
    'renders tooltip for %s placement',
    (placement) => {
      vi.useFakeTimers();
      render(
        <Tooltip content="Help text" delay={0} placement={placement}>
          <button>btn</button>
        </Tooltip>,
      );

      act(() => {
        fireEvent.mouseEnter(screen.getByRole('button'));
        vi.advanceTimersByTime(50);
      });

      expect(screen.getByText('Help text')).toBeInTheDocument();
      const portal = document.body.querySelector('div.fixed') as HTMLElement;
      expect(portal).toBeTruthy();

      vi.useRealTimers();
    },
  );

  it('does not render when content is empty', () => {
    vi.useFakeTimers();
    render(
      <Tooltip content="" delay={10}>
        <button>btn</button>
      </Tooltip>,
    );
    act(() => {
      fireEvent.mouseEnter(screen.getByRole('button'));
      vi.advanceTimersByTime(50);
    });
    expect(document.body.querySelector('div.fixed')).toBeNull();
    vi.useRealTimers();
  });

  it('forwards ref to a function-ref child and calls existing handlers', () => {
    vi.useFakeTimers();
    const childRef = vi.fn();
    const childMouseEnter = vi.fn();
    const childMouseLeave = vi.fn();
    const childFocus = vi.fn();
    const childBlur = vi.fn();

    const Btn = React.forwardRef<HTMLButtonElement, any>((props, ref) => (
      <button ref={ref} {...props}>btn</button>
    ));

    render(
      <Tooltip content="Help text" delay={10}>
        <Btn
          ref={childRef}
          onMouseEnter={childMouseEnter}
          onMouseLeave={childMouseLeave}
          onFocus={childFocus}
          onBlur={childBlur}
        />
      </Tooltip>,
    );

    expect(childRef).toHaveBeenCalled();

    const btn = screen.getByRole('button');
    act(() => {
      fireEvent.mouseEnter(btn);
    });
    expect(childMouseEnter).toHaveBeenCalled();

    act(() => {
      fireEvent.mouseLeave(btn);
    });
    expect(childMouseLeave).toHaveBeenCalled();

    act(() => {
      fireEvent.focus(btn);
    });
    expect(childFocus).toHaveBeenCalled();

    act(() => {
      fireEvent.blur(btn);
    });
    expect(childBlur).toHaveBeenCalled();

    vi.useRealTimers();
  });

  it('handles the case where the trigger ref is null when the timeout fires', () => {
    vi.useFakeTimers();
    const { unmount } = render(
      <Tooltip content="Help text" delay={200}>
        <button>btn</button>
      </Tooltip>,
    );

    act(() => {
      fireEvent.mouseEnter(screen.getByRole('button'));
    });
    // Unmount before the timeout fires — ref callback should set triggerRef to null
    unmount();
    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(document.body.querySelector('div.fixed')).toBeNull();
    vi.useRealTimers();
  });

  it('forwards ref to an object-ref child', () => {
    const childRef = React.createRef<HTMLButtonElement>();
    const Btn = React.forwardRef<HTMLButtonElement, any>((props, ref) => (
      <button ref={ref} {...props}>btn</button>
    ));

    render(
      <Tooltip content="Help text">
        <Btn ref={childRef} />
      </Tooltip>,
    );

    expect(childRef.current).toBeInstanceOf(HTMLButtonElement);
  });
});
