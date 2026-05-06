import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import React from 'react';
import { useCanvasTouchGestures } from './useCanvasTouchGestures';

interface Point { x: number; y: number }

// React.TouchEvent is a synthetic-event type; test handlers don't read the
// React-only fields, only `touches` + `preventDefault`. Build a minimal
// stand-in object rather than fight jsdom's missing TouchEvent constructor.
function makeTouchEvent(touches: Array<{ clientX: number; clientY: number }>) {
  return {
    touches: touches.map((t) => ({ clientX: t.clientX, clientY: t.clientY })),
    preventDefault: vi.fn(),
  } as unknown as React.TouchEvent;
}

describe('useCanvasTouchGestures', () => {
  let containerRef: React.RefObject<HTMLDivElement>;
  let setOffsetCalls: Array<((p: Point) => Point) | Point>;
  let setIsPanningCalls: boolean[];
  let clampZoom: ReturnType<typeof vi.fn>;
  let applyZoomAtPoint: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    // Provide a non-null container ref so the hook proceeds past its early-out.
    const div = document.createElement('div');
    containerRef = { current: div } as React.RefObject<HTMLDivElement>;
    setOffsetCalls = [];
    setIsPanningCalls = [];
    clampZoom = vi.fn((v: number) => Math.max(0.1, Math.min(5, v)));
    applyZoomAtPoint = vi.fn(
      (newZoom: number, _x: number, _y: number, _baseZoom: number, baseOffset: Point) =>
        ({ x: baseOffset.x + newZoom, y: baseOffset.y + newZoom })
    );
  });

  function setup({
    zoom = 1,
    offset = { x: 0, y: 0 } as Point,
    editingText = null as { id: string; x: number; y: number } | null,
  } = {}) {
    return renderHook(() =>
      useCanvasTouchGestures({
        containerRef,
        zoom,
        offset,
        setOffset: ((updater: ((p: Point) => Point) | Point) => {
          setOffsetCalls.push(updater);
        }) as React.Dispatch<React.SetStateAction<Point>>,
        setIsPanning: ((v: boolean) => {
          setIsPanningCalls.push(v);
        }) as React.Dispatch<React.SetStateAction<boolean>>,
        editingText,
        clampZoom,
        applyZoomAtPoint,
      })
    );
  }

  it('does nothing on touch start when containerRef is null', () => {
    containerRef = { current: null } as React.RefObject<HTMLDivElement>;
    const { result } = setup();
    const evt = makeTouchEvent([{ clientX: 10, clientY: 10 }]);

    act(() => result.current.handleTouchStart(evt));

    expect(setIsPanningCalls).toEqual([]);
    expect(evt.preventDefault).not.toHaveBeenCalled();
  });

  it('starts a single-finger pan on touchstart', () => {
    const { result } = setup();
    const evt = makeTouchEvent([{ clientX: 100, clientY: 50 }]);

    act(() => result.current.handleTouchStart(evt));

    expect(setIsPanningCalls).toEqual([true]);
    expect(evt.preventDefault).toHaveBeenCalled();
  });

  it('does not start a single-finger pan while editing text', () => {
    const { result } = setup({ editingText: { id: 'n1', x: 0, y: 0 } });
    const evt = makeTouchEvent([{ clientX: 100, clientY: 50 }]);

    act(() => result.current.handleTouchStart(evt));

    expect(setIsPanningCalls).toEqual([]);
  });

  it('begins a pinch on two-finger touchstart and explicitly disables panning', () => {
    const { result } = setup();
    const evt = makeTouchEvent([
      { clientX: 0, clientY: 0 },
      { clientX: 100, clientY: 0 },
    ]);

    act(() => result.current.handleTouchStart(evt));

    expect(setIsPanningCalls).toEqual([false]);
    expect(evt.preventDefault).toHaveBeenCalled();
  });

  it('updates offset during a single-finger pan move', () => {
    const { result } = setup();
    act(() => result.current.handleTouchStart(makeTouchEvent([{ clientX: 100, clientY: 50 }])));

    const move = makeTouchEvent([{ clientX: 130, clientY: 70 }]);
    act(() => result.current.handleTouchMove(move));

    expect(setOffsetCalls).toHaveLength(1);
    const updater = setOffsetCalls[0] as (p: Point) => Point;
    expect(typeof updater).toBe('function');
    expect(updater({ x: 0, y: 0 })).toEqual({ x: 30, y: 20 });
    expect(move.preventDefault).toHaveBeenCalled();
  });

  it('does not update offset on touchmove without a prior single-finger touchstart', () => {
    const { result } = setup();
    const move = makeTouchEvent([{ clientX: 130, clientY: 70 }]);
    act(() => result.current.handleTouchMove(move));
    expect(setOffsetCalls).toHaveLength(0);
  });

  it('runs the pinch zoom math through clampZoom + applyZoomAtPoint', () => {
    const { result } = setup({ zoom: 1, offset: { x: 0, y: 0 } });
    act(() =>
      result.current.handleTouchStart(
        makeTouchEvent([
          { clientX: 0, clientY: 0 },
          { clientX: 100, clientY: 0 },
        ])
      )
    );

    const move = makeTouchEvent([
      { clientX: -50, clientY: 0 },
      { clientX: 150, clientY: 0 },
    ]);
    act(() => result.current.handleTouchMove(move));

    expect(clampZoom).toHaveBeenCalledWith(2);
    expect(applyZoomAtPoint).toHaveBeenCalledTimes(1);
    const [newZoomArg, midX, midY, baseZoom, baseOffset] = applyZoomAtPoint.mock.calls[0];
    expect(newZoomArg).toBe(2);
    expect(midX).toBe(50);
    expect(midY).toBe(0);
    expect(baseZoom).toBe(1);
    expect(baseOffset).toEqual({ x: 0, y: 0 });
    expect(move.preventDefault).toHaveBeenCalled();
  });

  it('on touchend with no remaining fingers, clears panning state', () => {
    const { result } = setup();
    act(() => result.current.handleTouchStart(makeTouchEvent([{ clientX: 0, clientY: 0 }])));
    setIsPanningCalls.length = 0;

    act(() => result.current.handleTouchEnd(makeTouchEvent([])));

    expect(setIsPanningCalls).toEqual([false]);
  });

  it('on touchend with one remaining finger, hands off to a single-finger pan', () => {
    const { result } = setup();
    act(() =>
      result.current.handleTouchStart(
        makeTouchEvent([
          { clientX: 0, clientY: 0 },
          { clientX: 100, clientY: 0 },
        ])
      )
    );
    setIsPanningCalls.length = 0;

    act(() => result.current.handleTouchEnd(makeTouchEvent([{ clientX: 50, clientY: 0 }])));

    expect(setIsPanningCalls).toEqual([true]);
  });
});
