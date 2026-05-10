import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, render, act } from '@testing-library/react';
import React, { useRef } from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

describe('useCanvasAnimation', () => {
  let rafCallbacks: Array<FrameRequestCallback>;
  let nextRafId: number;
  let cancelledIds: number[];

  beforeEach(() => {
    rafCallbacks = [];
    nextRafId = 1;
    cancelledIds = [];

    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      rafCallbacks.push(cb);
      return nextRafId++;
    });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => {
      cancelledIds.push(id);
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function tickFrame() {
    const cb = rafCallbacks.shift();
    if (cb) act(() => cb(performance.now()));
  }

  // Render the hook against a real <canvas> mounted in jsdom so canvas.getContext
  // returns a working 2d context (jsdom provides a stub implementation).
  function setup({
    active = true,
    onInit,
    onFrame = () => {},
    onCleanup,
    deps = [] as React.DependencyList,
  }: {
    active?: boolean;
    onInit?: (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement) => void;
    onFrame?: (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement) => void | false;
    onCleanup?: () => void;
    deps?: React.DependencyList;
  } = {}) {
    function Harness({ activeProp }: { activeProp: boolean }) {
      const ref = useRef<HTMLCanvasElement | null>(null);
      useCanvasAnimation(ref as React.RefObject<HTMLCanvasElement>, activeProp, {
        onInit,
        onFrame,
        onCleanup,
      }, deps);
      return <canvas ref={ref} />;
    }
    const utils = render(<Harness activeProp={active} />);
    const canvas = utils.container.querySelector('canvas')!;
    return { ...utils, canvas };
  }

  it('does nothing when active is false', () => {
    const onInit = vi.fn();
    const onFrame = vi.fn();
    setup({ active: false, onInit, onFrame });

    expect(onInit).not.toHaveBeenCalled();
    expect(rafCallbacks.length).toBe(0);
  });

  it('sizes the canvas to window dimensions and calls onInit', () => {
    const onInit = vi.fn();
    const { canvas } = setup({ onInit });
    expect(canvas.width).toBe(window.innerWidth);
    expect(canvas.height).toBe(window.innerHeight);
    expect(onInit).toHaveBeenCalledTimes(1);
    const [ctx, passedCanvas] = onInit.mock.calls[0];
    expect(passedCanvas).toBe(canvas);
    expect(ctx).toBeTruthy();
  });

  it('schedules requestAnimationFrame and invokes onFrame each tick', () => {
    const onFrame = vi.fn();
    setup({ onFrame });
    expect(rafCallbacks.length).toBe(1);

    tickFrame();
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(rafCallbacks.length).toBe(1);

    tickFrame();
    expect(onFrame).toHaveBeenCalledTimes(2);
  });

  it('stops the loop when onFrame returns false', () => {
    let calls = 0;
    const onFrame = vi.fn(() => {
      calls += 1;
      return calls >= 2 ? false : undefined;
    });
    setup({ onFrame });

    tickFrame();
    expect(rafCallbacks.length).toBe(1);

    tickFrame();
    expect(onFrame).toHaveBeenCalledTimes(2);
    expect(rafCallbacks.length).toBe(0);
  });

  it('re-runs onInit on window resize', () => {
    const onInit = vi.fn();
    setup({ onInit });
    expect(onInit).toHaveBeenCalledTimes(1);

    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(onInit).toHaveBeenCalledTimes(2);
  });

  it('cancels the frame, removes the listener, and calls onCleanup on unmount', () => {
    const onCleanup = vi.fn();
    const onInit = vi.fn();
    const { unmount } = setup({ onInit, onCleanup });
    expect(rafCallbacks.length).toBe(1);
    const initialFrameId = nextRafId - 1;
    const initialInitCalls = onInit.mock.calls.length;

    unmount();
    expect(cancelledIds).toContain(initialFrameId);
    expect(onCleanup).toHaveBeenCalledTimes(1);

    // Resize listener removed: no further onInit after unmount.
    act(() => window.dispatchEvent(new Event('resize')));
    expect(onInit.mock.calls.length).toBe(initialInitCalls);
  });

  it('survives a missing canvas ref by no-oping cleanly', () => {
    function Harness() {
      const ref = useRef<HTMLCanvasElement | null>(null);
      // Pass the ref but never attach it to a DOM element.
      useCanvasAnimation(ref as React.RefObject<HTMLCanvasElement>, true, {
        onFrame: () => {},
      });
      return null;
    }
    expect(() => render(<Harness />)).not.toThrow();
    expect(rafCallbacks.length).toBe(0);
  });

  it('falls back gracefully when getContext returns null', () => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function () {
      return null as unknown as CanvasRenderingContext2D;
    };
    try {
      const onInit = vi.fn();
      setup({ onInit });
      expect(onInit).not.toHaveBeenCalled();
      expect(rafCallbacks.length).toBe(0);
    } finally {
      HTMLCanvasElement.prototype.getContext = original;
    }
  });
});
