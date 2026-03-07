import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCanvasInteraction } from './useCanvasInteraction';
import type { Node, Annotation, DeviceNode } from '../../types';
import { DeviceType } from '../../types';

// Mock getBoundingClientRect
const mockRect = {
  left: 0,
  top: 0,
  right: 800,
  bottom: 600,
  width: 800,
  height: 600,
  x: 0,
  y: 0,
  toJSON: () => {},
};

// Factory functions
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

const createAnnotation = (overrides: Partial<Annotation> = {}): Annotation => ({
  id: 'ann-1',
  type: 'rect',
  x: 150,
  y: 150,
  width: 100,
  height: 60,
  ...overrides,
});

// Creates a mock React.MouseEvent with the specified properties
function createMouseEvent(overrides: Partial<React.MouseEvent> = {}): React.MouseEvent {
  return {
    button: 0,
    clientX: 100,
    clientY: 100,
    movementX: 0,
    movementY: 0,
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
    target: document.createElement('div'),
    ...overrides,
  } as unknown as React.MouseEvent;
}

function createWheelEvent(overrides: Partial<React.WheelEvent> = {}): React.WheelEvent {
  return {
    deltaX: 0,
    deltaY: 0,
    clientX: 400,
    clientY: 300,
    ctrlKey: false,
    metaKey: false,
    preventDefault: vi.fn(),
    ...overrides,
  } as unknown as React.WheelEvent;
}

function createDragEvent(type: string, data: Record<string, string> = {}): React.DragEvent {
  const types = Object.keys(data);
  return {
    preventDefault: vi.fn(),
    dataTransfer: {
      types,
      dropEffect: '' as string,
      getData: (key: string) => data[key] || '',
    },
    clientX: 400,
    clientY: 300,
  } as unknown as React.DragEvent;
}

function createTouchEvent(points: Array<{ clientX: number; clientY: number }>): React.TouchEvent {
  const touches = points.map((point, index) => ({
    identifier: index,
    clientX: point.clientX,
    clientY: point.clientY,
  }));
  return {
    touches,
    preventDefault: vi.fn(),
  } as unknown as React.TouchEvent;
}

describe('useCanvasInteraction', () => {
  const mockContainerRef = {
    current: {
      getBoundingClientRect: () => mockRect,
    },
  } as React.RefObject<HTMLDivElement>;

  const defaultArgs = {
    containerRef: mockContainerRef,
    zoom: 1,
    setZoom: vi.fn(),
    offset: { x: 0, y: 0 },
    setOffset: vi.fn(),
    nodes: [] as Node[],
    annotations: [] as Annotation[],
    activeTool: 'pointer' as const,
    onToolCreate: vi.fn(),
    onNodeMove: vi.fn(),
    onAnnotationMove: vi.fn(),
    onConnect: vi.fn(),
    onSelect: vi.fn(),
    onSelectMultiple: vi.fn(),
    onUpdateAnnotation: vi.fn(),
    onDropDevice: vi.fn(),
    onDropExternalNetwork: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -- Initial state --

  describe('Initial State', () => {
    it('returns initial state with no dragging/linking/panning', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      expect(result.current.draggingNode).toBeNull();
      expect(result.current.linkingNode).toBeNull();
      expect(result.current.isPanning).toBe(false);
      expect(result.current.isZooming).toBe(false);
      expect(result.current.resizing).toBeNull();
      expect(result.current.drawStart).toBeNull();
      expect(result.current.drawEnd).toBeNull();
      expect(result.current.editingText).toBeNull();
      expect(result.current.marqueeStart).toBeNull();
      expect(result.current.marqueeEnd).toBeNull();
    });

    it('returns mousePos at 0,0 initially', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      expect(result.current.mousePos).toEqual({ x: 0, y: 0 });
    });
  });

  // -- Node mouse handling --

  describe('Node Mouse Down', () => {
    it('starts dragging on left-click without shift', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleNodeMouseDown(createMouseEvent({ button: 0 }), 'node-1');
      });

      expect(result.current.draggingNode).toBe('node-1');
      expect(defaultArgs.onSelect).toHaveBeenCalledWith('node-1');
    });

    it('starts linking on shift+click', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleNodeMouseDown(createMouseEvent({ shiftKey: true }), 'node-1');
      });

      expect(result.current.linkingNode).toBe('node-1');
      expect(result.current.draggingNode).toBeNull();
    });

    it('ignores right-click on node', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleNodeMouseDown(createMouseEvent({ button: 2 }), 'node-1');
      });

      expect(result.current.draggingNode).toBeNull();
      expect(result.current.linkingNode).toBeNull();
    });

    it('stops propagation on node mousedown', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createMouseEvent();

      act(() => {
        result.current.handleNodeMouseDown(event, 'node-1');
      });

      expect(event.stopPropagation).toHaveBeenCalled();
    });
  });

  // -- Node Mouse Up --

  describe('Node Mouse Up', () => {
    it('completes connection when releasing on a different node while linking', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      // Start linking from node-1
      act(() => {
        result.current.handleNodeMouseDown(createMouseEvent({ shiftKey: true }), 'node-1');
      });

      // Release on node-2
      act(() => {
        result.current.handleNodeMouseUp(createMouseEvent(), 'node-2');
      });

      expect(defaultArgs.onConnect).toHaveBeenCalledWith('node-1', 'node-2');
    });

    it('does not connect when releasing on the same node', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleNodeMouseDown(createMouseEvent({ shiftKey: true }), 'node-1');
      });

      act(() => {
        result.current.handleNodeMouseUp(createMouseEvent(), 'node-1');
      });

      expect(defaultArgs.onConnect).not.toHaveBeenCalled();
    });

    it('clears linking node and dragging node on mouse up', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleNodeMouseDown(createMouseEvent({ shiftKey: true }), 'node-1');
      });

      act(() => {
        result.current.handleNodeMouseUp(createMouseEvent(), 'node-2');
      });

      expect(result.current.linkingNode).toBeNull();
      expect(result.current.draggingNode).toBeNull();
    });
  });

  // -- Link Mouse Down --

  describe('Link Mouse Down', () => {
    it('selects the link on left-click', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleLinkMouseDown(createMouseEvent({ button: 0 }), 'link-1');
      });

      expect(defaultArgs.onSelect).toHaveBeenCalledWith('link-1');
    });

    it('ignores right-click on link', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleLinkMouseDown(createMouseEvent({ button: 2 }), 'link-1');
      });

      expect(defaultArgs.onSelect).not.toHaveBeenCalled();
    });

    it('stops propagation on link mousedown', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createMouseEvent();

      act(() => {
        result.current.handleLinkMouseDown(event, 'link-1');
      });

      expect(event.stopPropagation).toHaveBeenCalled();
    });
  });

  // -- Annotation Mouse Down --

  describe('Annotation Mouse Down', () => {
    it('starts dragging annotation and selects it', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createMouseEvent();

      act(() => {
        result.current.handleAnnotationMouseDown(event, 'ann-1');
      });

      expect(defaultArgs.onSelect).toHaveBeenCalledWith('ann-1');
      expect(event.stopPropagation).toHaveBeenCalled();
    });

    it('uses annotation drag to pan in hand mode instead of selecting', () => {
      const args = { ...defaultArgs, activeTool: 'hand' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));
      const event = createMouseEvent({ button: 0, clientX: 120, clientY: 140 });

      act(() => {
        result.current.handleAnnotationMouseDown(event, 'ann-1');
      });

      expect(result.current.isPanning).toBe(true);
      expect(defaultArgs.onSelect).not.toHaveBeenCalled();
    });
  });

  // -- Canvas Mouse Down --

  describe('Canvas Mouse Down (handleMouseDown)', () => {
    it('starts panning on middle-click (button=1)', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleMouseDown(createMouseEvent({ button: 1 }));
      });

      expect(result.current.isPanning).toBe(true);
    });

    it('starts marquee tracking on left-click in pointer mode', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleMouseDown(createMouseEvent({ button: 0, clientX: 200, clientY: 200 }));
      });

      expect(result.current.marqueeStart).not.toBeNull();
    });

    it('starts draw gesture in rect tool mode', () => {
      const args = { ...defaultArgs, activeTool: 'rect' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));

      act(() => {
        result.current.handleMouseDown(createMouseEvent({ button: 0, clientX: 200, clientY: 200 }));
      });

      expect(result.current.drawStart).not.toBeNull();
      expect(result.current.drawEnd).not.toBeNull();
    });

    it('calls onToolCreate for text tool on left-click', () => {
      const args = { ...defaultArgs, activeTool: 'text' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));

      act(() => {
        result.current.handleMouseDown(createMouseEvent({ button: 0, clientX: 400, clientY: 300 }));
      });

      expect(defaultArgs.onToolCreate).toHaveBeenCalledWith('text', expect.any(Number), expect.any(Number));
    });

    it('starts panning on left-click in hand mode', () => {
      const args = { ...defaultArgs, activeTool: 'hand' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));

      act(() => {
        result.current.handleMouseDown(createMouseEvent({ button: 0, clientX: 200, clientY: 200 }));
      });

      expect(result.current.isPanning).toBe(true);
      expect(result.current.marqueeStart).toBeNull();
    });

    it('starts drag zoom on right-click in hand mode', () => {
      const args = { ...defaultArgs, activeTool: 'hand' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));
      const event = createMouseEvent({ button: 2, clientX: 200, clientY: 200 });

      act(() => {
        result.current.handleMouseDown(event);
      });

      expect(event.preventDefault).toHaveBeenCalled();
      expect(result.current.isZooming).toBe(true);
      expect(result.current.isPanning).toBe(false);
    });
  });

  // -- Mouse Up --

  describe('Canvas Mouse Up (handleMouseUp)', () => {
    it('deselects on click-release without panning in pointer mode', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      // Simulate click (down + up without moving)
      act(() => {
        result.current.handleMouseDown(createMouseEvent({ button: 0, clientX: 200, clientY: 200 }));
      });
      act(() => {
        result.current.handleMouseUp();
      });

      expect(defaultArgs.onSelect).toHaveBeenCalledWith(null);
    });

    it('resets all drag state on mouse up', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      // Start dragging a node
      act(() => {
        result.current.handleNodeMouseDown(createMouseEvent(), 'node-1');
      });
      expect(result.current.draggingNode).toBe('node-1');

      // Canvas mouse up clears everything
      act(() => {
        result.current.handleMouseUp();
      });

      // draggingNode is NOT cleared by handleMouseUp directly;
      // it gets cleared by handleNodeMouseUp or at the end of the general mouseUp.
      // But the general mouse up does clear it via marquee fallback.
      // The important thing: isPanning, linkingNode, resizing are cleared.
      expect(result.current.isPanning).toBe(false);
      expect(result.current.isZooming).toBe(false);
      expect(result.current.linkingNode).toBeNull();
      expect(result.current.resizing).toBeNull();
    });
  });

  // -- Wheel / Zoom --

  describe('Wheel / Zoom', () => {
    it('pans canvas with regular scroll (no ctrl)', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleWheel(createWheelEvent({ deltaX: 10, deltaY: 20 }));
      });

      expect(defaultArgs.setOffset).toHaveBeenCalledWith(expect.any(Function));
      // Verify the function reduces offset by delta
      const updater = defaultArgs.setOffset.mock.calls[0][0];
      const newOffset = updater({ x: 100, y: 100 });
      expect(newOffset).toEqual({ x: 90, y: 80 });
    });

    it('zooms with ctrl+scroll', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createWheelEvent({ ctrlKey: true, deltaY: -100 });

      act(() => {
        result.current.handleWheel(event);
      });

      expect(event.preventDefault).toHaveBeenCalled();
      expect(defaultArgs.setZoom).toHaveBeenCalled();
      expect(defaultArgs.setOffset).toHaveBeenCalled();
    });

    it('zooms with meta+scroll (macOS)', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createWheelEvent({ metaKey: true, deltaY: -100 });

      act(() => {
        result.current.handleWheel(event);
      });

      expect(event.preventDefault).toHaveBeenCalled();
      expect(defaultArgs.setZoom).toHaveBeenCalled();
    });

    it('updates zoom during right-drag zoom in hand mode', () => {
      const args = { ...defaultArgs, activeTool: 'hand' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));

      act(() => {
        result.current.handleMouseDown(createMouseEvent({ button: 2, clientX: 300, clientY: 300 }));
      });

      act(() => {
        result.current.handleMouseMove(createMouseEvent({ button: 2, clientX: 300, clientY: 320, movementY: 20 }));
      });

      expect(defaultArgs.setZoom).toHaveBeenCalled();
      expect(defaultArgs.setOffset).toHaveBeenCalled();
    });
  });

  // -- Touch support --

  describe('Touch Navigation', () => {
    it('starts one-finger pan in pointer mode for touchscreen laptops', () => {
      const args = { ...defaultArgs, activeTool: 'pointer' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));
      const event = createTouchEvent([{ clientX: 100, clientY: 120 }]);

      act(() => {
        result.current.handleTouchStart(event);
      });

      expect(event.preventDefault).toHaveBeenCalled();
      expect(result.current.isPanning).toBe(true);
    });

    it('updates offset during one-finger pan regardless of active tool', () => {
      const args = { ...defaultArgs, activeTool: 'rect' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));

      act(() => {
        result.current.handleTouchStart(createTouchEvent([{ clientX: 100, clientY: 120 }]));
      });

      act(() => {
        result.current.handleTouchMove(createTouchEvent([{ clientX: 130, clientY: 150 }]));
      });

      expect(defaultArgs.setOffset).toHaveBeenCalledWith(expect.any(Function));
      const updater = defaultArgs.setOffset.mock.calls.at(-1)?.[0];
      expect(updater({ x: 10, y: 15 })).toEqual({ x: 40, y: 45 });
    });

    it('updates zoom during two-finger pinch regardless of tool', () => {
      const args = { ...defaultArgs, activeTool: 'pointer' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));

      act(() => {
        result.current.handleTouchStart(createTouchEvent([
          { clientX: 100, clientY: 100 },
          { clientX: 200, clientY: 100 },
        ]));
      });

      act(() => {
        result.current.handleTouchMove(createTouchEvent([
          { clientX: 80, clientY: 100 },
          { clientX: 220, clientY: 100 },
        ]));
      });

      expect(defaultArgs.setZoom).toHaveBeenCalled();
      expect(defaultArgs.setOffset).toHaveBeenCalled();
    });

    it('does not start touch panning while inline text editing is active', () => {
      const args = { ...defaultArgs, activeTool: 'pointer' as const };
      const { result } = renderHook(() => useCanvasInteraction(args));
      const event = createTouchEvent([{ clientX: 100, clientY: 120 }]);

      act(() => {
        result.current.setEditingText({ id: 'ann-text', x: 10, y: 20 });
      });

      act(() => {
        result.current.handleTouchStart(event);
      });

      expect(result.current.isPanning).toBe(false);
      expect(event.preventDefault).not.toHaveBeenCalled();
    });
  });

  // -- Drag and Drop --

  describe('Drag and Drop', () => {
    it('accepts device drag over events', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createDragEvent('dragover', { 'application/x-archetype-device': '{}' });

      act(() => {
        result.current.handleDragOver(event);
      });

      expect(event.preventDefault).toHaveBeenCalled();
      expect(event.dataTransfer.dropEffect).toBe('copy');
    });

    it('accepts external network drag over events', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createDragEvent('dragover', { 'application/x-archetype-external': '1' });

      act(() => {
        result.current.handleDragOver(event);
      });

      expect(event.preventDefault).toHaveBeenCalled();
    });

    it('does not accept unknown drag types', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createDragEvent('dragover', { 'text/plain': 'hello' });

      act(() => {
        result.current.handleDragOver(event);
      });

      expect(event.preventDefault).not.toHaveBeenCalled();
    });

    it('calls onDropDevice when device data is dropped', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const deviceModel = { id: 'ceos', name: 'cEOS', type: 'router' };
      const event = createDragEvent('drop', {
        'application/x-archetype-device': JSON.stringify(deviceModel),
      });

      act(() => {
        result.current.handleDrop(event);
      });

      expect(event.preventDefault).toHaveBeenCalled();
      expect(defaultArgs.onDropDevice).toHaveBeenCalledWith(
        deviceModel,
        expect.any(Number),
        expect.any(Number)
      );
    });

    it('calls onDropExternalNetwork when external network data is dropped', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createDragEvent('drop', {
        'application/x-archetype-external': '1',
      });

      act(() => {
        result.current.handleDrop(event);
      });

      expect(defaultArgs.onDropExternalNetwork).toHaveBeenCalledWith(
        expect.any(Number),
        expect.any(Number)
      );
    });

    it('handles malformed device JSON gracefully on drop', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));
      const event = createDragEvent('drop', {
        'application/x-archetype-device': 'not-json',
      });

      // Should not throw
      act(() => {
        result.current.handleDrop(event);
      });

      expect(defaultArgs.onDropDevice).not.toHaveBeenCalled();
    });
  });

  // -- Resize cursors --

  describe('Resize Cursors', () => {
    it('returns correct cursor for each resize handle', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      expect(result.current.getResizeCursor('nw')).toBe('nwse-resize');
      expect(result.current.getResizeCursor('n')).toBe('ns-resize');
      expect(result.current.getResizeCursor('ne')).toBe('nesw-resize');
      expect(result.current.getResizeCursor('e')).toBe('ew-resize');
      expect(result.current.getResizeCursor('se')).toBe('nwse-resize');
      expect(result.current.getResizeCursor('s')).toBe('ns-resize');
      expect(result.current.getResizeCursor('sw')).toBe('nesw-resize');
      expect(result.current.getResizeCursor('w')).toBe('ew-resize');
    });
  });

  // -- Resize Mouse Down --

  describe('Resize Mouse Down', () => {
    it('starts resize state for an annotation', () => {
      const ann = createAnnotation({ id: 'ann-1', type: 'rect', x: 100, y: 100, width: 150, height: 80 });
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleResizeMouseDown(
          createMouseEvent({ clientX: 250, clientY: 100 }),
          ann,
          'e'
        );
      });

      expect(result.current.resizing).toEqual({
        id: 'ann-1',
        handle: 'e',
        startX: 250,
        startY: 100,
        startWidth: 150,
        startHeight: 80,
        startAnnX: 100,
        startAnnY: 100,
      });
    });

    it('defaults width for rect when not specified', () => {
      const ann: Annotation = { id: 'ann-2', type: 'rect', x: 50, y: 50 };
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleResizeMouseDown(
          createMouseEvent({ clientX: 150, clientY: 50 }),
          ann,
          'e'
        );
      });

      expect(result.current.resizing?.startWidth).toBe(100); // default for rect
    });

    it('defaults width for circle when not specified', () => {
      const ann: Annotation = { id: 'ann-3', type: 'circle', x: 50, y: 50 };
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.handleResizeMouseDown(
          createMouseEvent({ clientX: 130, clientY: 50 }),
          ann,
          'e'
        );
      });

      expect(result.current.resizing?.startWidth).toBe(80); // default for circle
    });
  });

  // -- Text editing refs --

  describe('Text Editing Refs', () => {
    it('exposes pendingTextEditRef and textEditCommittedRef', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      expect(result.current.pendingTextEditRef.current).toBe(false);
      expect(result.current.textEditCommittedRef.current).toBe(false);
    });

    it('exposes setEditingText to control inline text editing', () => {
      const { result } = renderHook(() => useCanvasInteraction(defaultArgs));

      act(() => {
        result.current.setEditingText({ id: 'ann-text', x: 100, y: 200 });
      });

      expect(result.current.editingText).toEqual({ id: 'ann-text', x: 100, y: 200 });
    });
  });
});
