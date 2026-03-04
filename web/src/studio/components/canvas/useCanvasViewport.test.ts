import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCanvasViewport } from './useCanvasViewport';
import type { Node, Annotation, DeviceNode, ExternalNetworkNode } from '../../types';
import { DeviceType } from '../../types';

// Mock getBoundingClientRect
const mockRect = {
  left: 0,
  top: 0,
  right: 1000,
  bottom: 800,
  width: 1000,
  height: 800,
  x: 0,
  y: 0,
  toJSON: () => {},
};

const createDeviceNode = (overrides: Partial<DeviceNode> = {}): DeviceNode => ({
  id: 'node-1',
  name: 'Router1',
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'ceos',
  version: '4.28.0F',
  x: 100,
  y: 100,
  ...overrides,
});

const createExternalNetworkNode = (overrides: Partial<ExternalNetworkNode> = {}): ExternalNetworkNode => ({
  id: 'ext-1',
  name: 'External1',
  nodeType: 'external',
  x: 200,
  y: 200,
  ...overrides,
});

const createAnnotation = (overrides: Partial<Annotation> = {}): Annotation => ({
  id: 'ann-1',
  type: 'rect',
  x: 150,
  y: 150,
  width: 100,
  height: 60,
  ...overrides,
});

describe('useCanvasViewport', () => {
  const containerRef = {
    current: {
      getBoundingClientRect: () => mockRect,
    },
  } as React.RefObject<HTMLDivElement>;

  const defaultArgs = {
    labId: 'lab-1',
    nodes: [] as Node[],
    annotations: [] as Annotation[],
    containerRef,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  // -- Initial State --

  describe('Initial State', () => {
    it('returns default zoom=1 and offset={0,0} when no stored viewport', () => {
      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      expect(result.current.zoom).toBe(1);
      expect(result.current.offset).toEqual({ x: 0, y: 0 });
    });

    it('restores viewport from localStorage when labId is provided', () => {
      localStorage.setItem(
        'archetype_canvas_viewport_lab-1',
        JSON.stringify({ zoom: 1.5, x: 50, y: -30 })
      );

      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      expect(result.current.zoom).toBe(1.5);
      expect(result.current.offset).toEqual({ x: 50, y: -30 });
    });

    it('returns default values when localStorage has invalid JSON', () => {
      localStorage.setItem('archetype_canvas_viewport_lab-1', 'not-json');

      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      expect(result.current.zoom).toBe(1);
      expect(result.current.offset).toEqual({ x: 0, y: 0 });
    });

    it('returns default values when labId is not provided', () => {
      const { result } = renderHook(() =>
        useCanvasViewport({ ...defaultArgs, labId: undefined })
      );

      expect(result.current.zoom).toBe(1);
      expect(result.current.offset).toEqual({ x: 0, y: 0 });
    });

    it('returns default values when stored viewport has wrong types', () => {
      localStorage.setItem(
        'archetype_canvas_viewport_lab-1',
        JSON.stringify({ zoom: 'bad', x: null, y: true })
      );

      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      expect(result.current.zoom).toBe(1);
      expect(result.current.offset).toEqual({ x: 0, y: 0 });
    });
  });

  // -- Setters --

  describe('Setters', () => {
    it('allows setting zoom via setZoom', () => {
      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      act(() => {
        result.current.setZoom(2);
      });

      expect(result.current.zoom).toBe(2);
    });

    it('allows setting offset via setOffset', () => {
      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      act(() => {
        result.current.setOffset({ x: 100, y: -50 });
      });

      expect(result.current.offset).toEqual({ x: 100, y: -50 });
    });
  });

  // -- Debounced Save --

  describe('Debounced Save to localStorage', () => {
    it('saves viewport to localStorage after debounce delay', async () => {
      vi.useFakeTimers();
      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      act(() => {
        result.current.setZoom(2.5);
      });

      // Before debounce fires
      expect(localStorage.getItem('archetype_canvas_viewport_lab-1')).toBeNull();

      // Fast-forward past debounce (300ms)
      act(() => {
        vi.advanceTimersByTime(300);
      });

      const stored = JSON.parse(localStorage.getItem('archetype_canvas_viewport_lab-1')!);
      expect(stored.zoom).toBe(2.5);

      vi.useRealTimers();
    });

    it('does not save to localStorage when labId is undefined', () => {
      vi.useFakeTimers();
      const { result } = renderHook(() =>
        useCanvasViewport({ ...defaultArgs, labId: undefined })
      );

      act(() => {
        result.current.setZoom(3);
      });

      act(() => {
        vi.advanceTimersByTime(500);
      });

      // No keys with our prefix should exist
      expect(localStorage.length).toBe(0);

      vi.useRealTimers();
    });
  });

  // -- centerCanvas --

  describe('centerCanvas', () => {
    it('resets to default when no content exists', () => {
      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      act(() => {
        result.current.setZoom(2);
        result.current.setOffset({ x: 500, y: 500 });
      });

      act(() => {
        result.current.centerCanvas();
      });

      expect(result.current.zoom).toBe(1);
      expect(result.current.offset).toEqual({ x: 0, y: 0 });
    });

    it('centers content without changing zoom when it fits at zoom=1', () => {
      // A single node at (200, 200) fits easily in a 1000x800 viewport
      const args = {
        ...defaultArgs,
        nodes: [createDeviceNode({ x: 200, y: 200 })],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.setOffset({ x: -999, y: -999 });
      });

      act(() => {
        result.current.centerCanvas();
      });

      // Zoom should remain at 1 since the content fits
      expect(result.current.zoom).toBe(1);
      // Offset should center the node in the viewport
      expect(result.current.offset.x).toBeGreaterThan(0);
      expect(result.current.offset.y).toBeGreaterThan(0);
    });

    it('zooms out when content is larger than viewport', () => {
      // Nodes spread far apart so they don't fit at zoom=1
      const args = {
        ...defaultArgs,
        nodes: [
          createDeviceNode({ id: 'n1', x: 0, y: 0 }),
          createDeviceNode({ id: 'n2', x: 2000, y: 1500 }),
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.centerCanvas();
      });

      // Should have zoomed out to fit
      expect(result.current.zoom).toBeLessThan(1);
      expect(result.current.zoom).toBeGreaterThan(0.1);
    });
  });

  // -- fitToScreen --

  describe('fitToScreen', () => {
    it('does nothing when no content exists', () => {
      const { result } = renderHook(() => useCanvasViewport(defaultArgs));

      act(() => {
        result.current.setZoom(2);
        result.current.setOffset({ x: 100, y: 100 });
      });

      act(() => {
        result.current.fitToScreen();
      });

      // Should remain unchanged since no content bounds
      expect(result.current.zoom).toBe(2);
    });

    it('fits nodes to screen with appropriate zoom', () => {
      const args = {
        ...defaultArgs,
        nodes: [
          createDeviceNode({ id: 'n1', x: 100, y: 100 }),
          createDeviceNode({ id: 'n2', x: 500, y: 400 }),
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.fitToScreen();
      });

      // Should zoom to fit with 0.9 padding factor, capped at 1
      expect(result.current.zoom).toBeGreaterThan(0);
      expect(result.current.zoom).toBeLessThanOrEqual(1);
    });

    it('fits annotations to screen as well as nodes', () => {
      const args = {
        ...defaultArgs,
        nodes: [createDeviceNode({ x: 100, y: 100 })],
        annotations: [
          createAnnotation({ type: 'rect', x: 800, y: 700, width: 200, height: 150 }),
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.fitToScreen();
      });

      // Should have considered the annotation bounds
      expect(result.current.zoom).toBeGreaterThan(0);
      expect(result.current.zoom).toBeLessThanOrEqual(1);
    });
  });

  // -- Content Bounds Calculation --

  describe('Content Bounds (via centerCanvas/fitToScreen behavior)', () => {
    it('accounts for circle annotation bounds', () => {
      const args = {
        ...defaultArgs,
        annotations: [
          createAnnotation({ type: 'circle', x: 500, y: 400, width: 200 }), // radius 100
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.fitToScreen();
      });

      // Circle extends from (400,300) to (600,500). Should fit at zoom <= 1
      expect(result.current.zoom).toBeGreaterThan(0);
    });

    it('accounts for text annotation bounds', () => {
      const args = {
        ...defaultArgs,
        annotations: [
          createAnnotation({ type: 'text', x: 100, y: 100, text: 'Hello World', fontSize: 20 }),
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.centerCanvas();
      });

      // Should have computed bounds from text annotation
      expect(result.current.zoom).toBeGreaterThan(0);
    });

    it('accounts for arrow annotation bounds', () => {
      const args = {
        ...defaultArgs,
        annotations: [
          createAnnotation({ type: 'arrow', x: 100, y: 100, targetX: 900, targetY: 700 }),
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.fitToScreen();
      });

      // Should have considered both arrow endpoints
      expect(result.current.zoom).toBeGreaterThan(0);
      expect(result.current.zoom).toBeLessThanOrEqual(1);
    });

    it('accounts for external network nodes with different padding', () => {
      const args = {
        ...defaultArgs,
        nodes: [
          createExternalNetworkNode({ x: 500, y: 400 }),
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.centerCanvas();
      });

      // External nodes use halfW=28, halfH=20 (different from device 24,24)
      expect(result.current.zoom).toBe(1); // should fit easily
    });

    it('handles annotations with default arrow target when targetX/Y not set', () => {
      const args = {
        ...defaultArgs,
        annotations: [
          createAnnotation({ type: 'arrow', x: 100, y: 100 }),
          // No targetX/targetY — defaults to x+100, y+100
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.fitToScreen();
      });

      // Arrow defaults: (100,100) to (200,200)
      expect(result.current.zoom).toBeGreaterThan(0);
    });

    it('handles unknown annotation type as small point', () => {
      const args = {
        ...defaultArgs,
        annotations: [
          { id: 'unk-1', type: 'polygon' as any, x: 300, y: 300 },
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.centerCanvas();
      });

      // Should treat unknown as a small 40x40 point, which fits easily
      expect(result.current.zoom).toBe(1);
    });
  });

  // -- Focus Node --

  describe('Focus Node', () => {
    it('pans to center the focused node', () => {
      const node = createDeviceNode({ id: 'node-1', x: 500, y: 400 });
      const args = {
        ...defaultArgs,
        nodes: [node],
        focusNodeId: 'node-1',
        onFocusHandled: vi.fn(),
      };

      const { result } = renderHook(() => useCanvasViewport(args));

      // The offset should center node-1 in the viewport
      // viewport center = (1000/2, 800/2) = (500, 400)
      // offset = center - node * zoom = (500 - 500*1, 400 - 400*1) = (0, 0)
      expect(result.current.offset.x).toBe(0);
      expect(result.current.offset.y).toBe(0);
      expect(args.onFocusHandled).toHaveBeenCalled();
    });

    it('does not pan when focusNodeId is null', () => {
      const onFocusHandled = vi.fn();
      const { result } = renderHook(() =>
        useCanvasViewport({ ...defaultArgs, focusNodeId: null, onFocusHandled })
      );

      expect(result.current.offset).toEqual({ x: 0, y: 0 });
      expect(onFocusHandled).not.toHaveBeenCalled();
    });

    it('does not pan when focused node does not exist', () => {
      const onFocusHandled = vi.fn();
      renderHook(() =>
        useCanvasViewport({
          ...defaultArgs,
          nodes: [createDeviceNode({ id: 'node-1' })],
          focusNodeId: 'nonexistent',
          onFocusHandled,
        })
      );

      expect(onFocusHandled).not.toHaveBeenCalled();
    });

    it('accounts for zoom level when centering focused node', () => {
      const node = createDeviceNode({ id: 'node-1', x: 300, y: 200 });

      // Pre-set a zoom of 2 via localStorage
      localStorage.setItem(
        'archetype_canvas_viewport_lab-1',
        JSON.stringify({ zoom: 2, x: 0, y: 0 })
      );

      const onFocusHandled = vi.fn();
      const { result } = renderHook(() =>
        useCanvasViewport({
          ...defaultArgs,
          nodes: [node],
          focusNodeId: 'node-1',
          onFocusHandled,
        })
      );

      // At zoom=2: offset = center - node * zoom = (500 - 300*2, 400 - 200*2) = (-100, 0)
      expect(result.current.offset.x).toBe(-100);
      expect(result.current.offset.y).toBe(0);
      expect(onFocusHandled).toHaveBeenCalled();
    });
  });

  // -- Viewport Persistence on Unmount --

  describe('Viewport Persistence on Unmount', () => {
    it('saves viewport to localStorage on unmount', () => {
      vi.useFakeTimers();
      const { result, unmount } = renderHook(() => useCanvasViewport(defaultArgs));

      act(() => {
        result.current.setZoom(1.8);
        result.current.setOffset({ x: 42, y: -17 });
      });

      unmount();

      const stored = JSON.parse(localStorage.getItem('archetype_canvas_viewport_lab-1')!);
      expect(stored.zoom).toBe(1.8);
      expect(stored.x).toBe(42);
      expect(stored.y).toBe(-17);

      vi.useRealTimers();
    });

    it('does not save on unmount when labId is undefined', () => {
      const { unmount } = renderHook(() =>
        useCanvasViewport({ ...defaultArgs, labId: undefined })
      );

      unmount();

      expect(localStorage.length).toBe(0);
    });
  });

  // -- Zoom constraints (via centerCanvas) --

  describe('Zoom Constraints', () => {
    it('enforces minimum zoom of 0.1 when fitting very large content', () => {
      const args = {
        ...defaultArgs,
        nodes: [
          createDeviceNode({ id: 'n1', x: 0, y: 0 }),
          createDeviceNode({ id: 'n2', x: 50000, y: 50000 }),
        ],
      };
      const { result } = renderHook(() => useCanvasViewport(args));

      act(() => {
        result.current.centerCanvas();
      });

      expect(result.current.zoom).toBeGreaterThanOrEqual(0.1);
    });
  });
});
