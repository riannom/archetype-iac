import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import React from 'react';
import { useCanvasDragDrop } from './useCanvasDragDrop';
import { DeviceType, type DeviceModel } from '../../types';

const makeContainerRef = (
  rect: Partial<DOMRect> = { left: 10, top: 20 },
): React.RefObject<HTMLDivElement | null> => {
  const el = {
    getBoundingClientRect: () => ({ left: 0, top: 0, ...rect } as DOMRect),
  } as unknown as HTMLDivElement;
  return { current: el };
};

const makeEvent = (overrides: {
  types?: string[];
  data?: Record<string, string>;
  clientX?: number;
  clientY?: number;
}) => {
  const types = overrides.types ?? [];
  const data = overrides.data ?? {};
  const dataTransfer = {
    types,
    dropEffect: 'none' as string,
    getData: (type: string) => data[type] ?? '',
  };
  const event = {
    dataTransfer,
    clientX: overrides.clientX ?? 0,
    clientY: overrides.clientY ?? 0,
    preventDefault: vi.fn(),
  };
  return event as unknown as React.DragEvent;
};

const deviceModel: DeviceModel = {
  id: 'router-1',
  type: DeviceType.ROUTER,
  name: 'cEOS',
  icon: 'router',
  versions: ['latest'],
  isActive: true,
  vendor: 'arista',
};

describe('useCanvasDragDrop', () => {
  describe('handleDragOver', () => {
    it('calls preventDefault and sets copy effect for device drags', () => {
      const { result } = renderHook(() =>
        useCanvasDragDrop({ containerRef: makeContainerRef(), offset: { x: 0, y: 0 }, zoom: 1 }),
      );
      const ev = makeEvent({ types: ['application/x-archetype-device'] });
      result.current.handleDragOver(ev);
      expect(ev.preventDefault).toHaveBeenCalled();
      expect(ev.dataTransfer.dropEffect).toBe('copy');
    });

    it('also accepts external network drags', () => {
      const { result } = renderHook(() =>
        useCanvasDragDrop({ containerRef: makeContainerRef(), offset: { x: 0, y: 0 }, zoom: 1 }),
      );
      const ev = makeEvent({ types: ['application/x-archetype-external'] });
      result.current.handleDragOver(ev);
      expect(ev.preventDefault).toHaveBeenCalled();
      expect(ev.dataTransfer.dropEffect).toBe('copy');
    });

    it('does nothing for unrelated drag types', () => {
      const { result } = renderHook(() =>
        useCanvasDragDrop({ containerRef: makeContainerRef(), offset: { x: 0, y: 0 }, zoom: 1 }),
      );
      const ev = makeEvent({ types: ['text/plain'] });
      result.current.handleDragOver(ev);
      expect(ev.preventDefault).not.toHaveBeenCalled();
      expect(ev.dataTransfer.dropEffect).toBe('none');
    });
  });

  describe('handleDrop', () => {
    it('calls onDropDevice with model and canvas-space coords', () => {
      const onDropDevice = vi.fn();
      const { result } = renderHook(() =>
        useCanvasDragDrop({
          containerRef: makeContainerRef({ left: 10, top: 20 }),
          offset: { x: 5, y: 10 },
          zoom: 2,
          onDropDevice,
        }),
      );
      const ev = makeEvent({
        data: { 'application/x-archetype-device': JSON.stringify(deviceModel) },
        clientX: 100,
        clientY: 200,
      });
      result.current.handleDrop(ev);

      expect(ev.preventDefault).toHaveBeenCalled();
      // x = (100 - 10 - 5) / 2 = 42.5; y = (200 - 20 - 10) / 2 = 85
      expect(onDropDevice).toHaveBeenCalledWith(expect.objectContaining({ id: 'router-1' }), 42.5, 85);
    });

    it('ignores invalid JSON in device data', () => {
      const onDropDevice = vi.fn();
      const onDropExternalNetwork = vi.fn();
      const { result } = renderHook(() =>
        useCanvasDragDrop({
          containerRef: makeContainerRef(),
          offset: { x: 0, y: 0 },
          zoom: 1,
          onDropDevice,
          onDropExternalNetwork,
        }),
      );
      const ev = makeEvent({
        data: { 'application/x-archetype-device': '{not-json' },
      });
      result.current.handleDrop(ev);
      expect(onDropDevice).not.toHaveBeenCalled();
      // Should NOT fall through to external — the device branch returns early
      expect(onDropExternalNetwork).not.toHaveBeenCalled();
    });

    it('skips device path when onDropDevice is not provided', () => {
      const onDropExternalNetwork = vi.fn();
      const { result } = renderHook(() =>
        useCanvasDragDrop({
          containerRef: makeContainerRef(),
          offset: { x: 0, y: 0 },
          zoom: 1,
          onDropExternalNetwork,
        }),
      );
      const ev = makeEvent({
        data: {
          'application/x-archetype-device': JSON.stringify(deviceModel),
          'application/x-archetype-external': '1',
        },
      });
      result.current.handleDrop(ev);
      // Device handler missing → falls through to external
      expect(onDropExternalNetwork).toHaveBeenCalled();
    });

    it('calls onDropExternalNetwork for external network drops', () => {
      const onDropExternalNetwork = vi.fn();
      const { result } = renderHook(() =>
        useCanvasDragDrop({
          containerRef: makeContainerRef({ left: 0, top: 0 }),
          offset: { x: 0, y: 0 },
          zoom: 1,
          onDropExternalNetwork,
        }),
      );
      const ev = makeEvent({
        data: { 'application/x-archetype-external': '1' },
        clientX: 50,
        clientY: 60,
      });
      result.current.handleDrop(ev);
      expect(onDropExternalNetwork).toHaveBeenCalledWith(50, 60);
    });

    it('does nothing when there is no container', () => {
      const onDropDevice = vi.fn();
      const onDropExternalNetwork = vi.fn();
      const { result } = renderHook(() =>
        useCanvasDragDrop({
          containerRef: { current: null },
          offset: { x: 0, y: 0 },
          zoom: 1,
          onDropDevice,
          onDropExternalNetwork,
        }),
      );
      const ev = makeEvent({
        data: { 'application/x-archetype-device': JSON.stringify(deviceModel) },
      });
      result.current.handleDrop(ev);
      expect(onDropDevice).not.toHaveBeenCalled();
      expect(onDropExternalNetwork).not.toHaveBeenCalled();
    });

    it('does nothing when there is no recognized drag data', () => {
      const onDropDevice = vi.fn();
      const onDropExternalNetwork = vi.fn();
      const { result } = renderHook(() =>
        useCanvasDragDrop({
          containerRef: makeContainerRef(),
          offset: { x: 0, y: 0 },
          zoom: 1,
          onDropDevice,
          onDropExternalNetwork,
        }),
      );
      const ev = makeEvent({ data: {} });
      result.current.handleDrop(ev);
      expect(onDropDevice).not.toHaveBeenCalled();
      expect(onDropExternalNetwork).not.toHaveBeenCalled();
    });

    it('skips external path when onDropExternalNetwork is not provided', () => {
      const { result } = renderHook(() =>
        useCanvasDragDrop({
          containerRef: makeContainerRef(),
          offset: { x: 0, y: 0 },
          zoom: 1,
        }),
      );
      const ev = makeEvent({ data: { 'application/x-archetype-external': '1' } });
      // Should be a no-op without throwing
      expect(() => result.current.handleDrop(ev)).not.toThrow();
    });
  });
});
