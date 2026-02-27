import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import React from 'react';
import { DragProvider, useDragContext } from './DragContext';

// Mock the api module
vi.mock('../../api', () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from '../../api';

const mockedApiRequest = vi.mocked(apiRequest);

function wrapper({ children, onImageAssigned }: { children: React.ReactNode; onImageAssigned?: () => void }) {
  return <DragProvider onImageAssigned={onImageAssigned}>{children}</DragProvider>;
}

function renderDragContext(onImageAssigned?: () => void) {
  return renderHook(() => useDragContext(), {
    wrapper: ({ children }) => wrapper({ children, onImageAssigned }),
  });
}

const testImage = {
  id: 'img-1',
  kind: 'docker',
  reference: 'ceos:4.30',
  filename: 'ceos-4.30.tar',
  device_id: 'ceos',
  version: '4.30',
  vendor: 'arista',
  size_bytes: 1024000,
};

describe('DragContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ---- startDrag / endDrag ----

  describe('startDrag / endDrag', () => {
    it('sets isDragging to true and stores image data', () => {
      const { result } = renderDragContext();

      expect(result.current.dragState.isDragging).toBe(false);
      expect(result.current.dragState.draggedImageData).toBeNull();

      act(() => {
        result.current.startDrag(testImage);
      });

      expect(result.current.dragState.isDragging).toBe(true);
      expect(result.current.dragState.draggedImageId).toBe('img-1');
      expect(result.current.dragState.draggedImageData).toEqual(testImage);
    });

    it('clears all drag state on endDrag', () => {
      const { result } = renderDragContext();

      act(() => {
        result.current.startDrag(testImage);
      });
      expect(result.current.dragState.isDragging).toBe(true);

      act(() => {
        result.current.endDrag();
      });

      expect(result.current.dragState.isDragging).toBe(false);
      expect(result.current.dragState.draggedImageId).toBeNull();
      expect(result.current.dragState.draggedImageData).toBeNull();
      expect(result.current.dragState.dragOverDeviceId).toBeNull();
      expect(result.current.dragState.isValidTarget).toBe(false);
    });
  });

  // ---- setDragOverDevice ----

  describe('setDragOverDevice', () => {
    it('updates deviceId and isValidTarget', () => {
      const { result } = renderDragContext();

      act(() => {
        result.current.startDrag(testImage);
      });

      act(() => {
        result.current.setDragOverDevice('device-abc', true);
      });

      expect(result.current.dragState.dragOverDeviceId).toBe('device-abc');
      expect(result.current.dragState.isValidTarget).toBe(true);
    });

    it('clears dragOverDevice when set to null', () => {
      const { result } = renderDragContext();

      act(() => {
        result.current.startDrag(testImage);
        result.current.setDragOverDevice('device-abc', true);
      });

      act(() => {
        result.current.setDragOverDevice(null, false);
      });

      expect(result.current.dragState.dragOverDeviceId).toBeNull();
      expect(result.current.dragState.isValidTarget).toBe(false);
    });
  });

  // ---- assignImageToDevice ----

  describe('assignImageToDevice', () => {
    it('calls POST /assign with encoded imageId and fires onImageAssigned', async () => {
      mockedApiRequest.mockResolvedValueOnce({});
      const onAssigned = vi.fn();
      const { result } = renderDragContext(onAssigned);

      await act(async () => {
        await result.current.assignImageToDevice('img/special', 'ceos');
      });

      expect(mockedApiRequest).toHaveBeenCalledWith(
        `/images/library/${encodeURIComponent('img/special')}/assign`,
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ device_id: 'ceos', is_default: false }),
        })
      );
      expect(onAssigned).toHaveBeenCalledTimes(1);
    });

    it('passes isDefault flag', async () => {
      mockedApiRequest.mockResolvedValueOnce({});
      const { result } = renderDragContext();

      await act(async () => {
        await result.current.assignImageToDevice('img-1', 'ceos', true);
      });

      expect(mockedApiRequest).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          body: JSON.stringify({ device_id: 'ceos', is_default: true }),
        })
      );
    });

    it('propagates API errors', async () => {
      mockedApiRequest.mockRejectedValueOnce(new Error('Assign failed'));
      const { result } = renderDragContext();

      await expect(
        act(async () => {
          await result.current.assignImageToDevice('img-1', 'ceos');
        })
      ).rejects.toThrow('Assign failed');
    });
  });

  // ---- unassignImage ----

  describe('unassignImage', () => {
    it('calls POST /unassign with deviceId in body', async () => {
      mockedApiRequest.mockResolvedValueOnce({});
      const onAssigned = vi.fn();
      const { result } = renderDragContext(onAssigned);

      await act(async () => {
        await result.current.unassignImage('img-1', 'ceos');
      });

      expect(mockedApiRequest).toHaveBeenCalledWith(
        `/images/library/${encodeURIComponent('img-1')}/unassign`,
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ device_id: 'ceos' }),
        })
      );
      expect(onAssigned).toHaveBeenCalledTimes(1);
    });

    it('sends undefined body when no deviceId provided', async () => {
      mockedApiRequest.mockResolvedValueOnce({});
      const { result } = renderDragContext();

      await act(async () => {
        await result.current.unassignImage('img-1');
      });

      expect(mockedApiRequest).toHaveBeenCalledWith(
        expect.stringContaining('/unassign'),
        expect.objectContaining({
          method: 'POST',
          body: undefined,
        })
      );
    });
  });

  // ---- deleteImage ----

  describe('deleteImage', () => {
    it('calls DELETE with encoded imageId and fires callback', async () => {
      mockedApiRequest.mockResolvedValueOnce({});
      const onAssigned = vi.fn();
      const { result } = renderDragContext(onAssigned);

      await act(async () => {
        await result.current.deleteImage('img/with-slash');
      });

      expect(mockedApiRequest).toHaveBeenCalledWith(
        `/images/library/${encodeURIComponent('img/with-slash')}`,
        expect.objectContaining({ method: 'DELETE' })
      );
      expect(onAssigned).toHaveBeenCalledTimes(1);
    });

    it('propagates API errors', async () => {
      mockedApiRequest.mockRejectedValueOnce(new Error('Delete failed'));
      const { result } = renderDragContext();

      await expect(
        act(async () => {
          await result.current.deleteImage('img-1');
        })
      ).rejects.toThrow('Delete failed');
    });
  });

  // ---- Context guard ----

  describe('useDragContext outside provider', () => {
    it('throws when used outside DragProvider', () => {
      // Suppress console.error from React for this expected error
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

      expect(() => {
        renderHook(() => useDragContext());
      }).toThrow('useDragContext must be used within a DragProvider');

      spy.mockRestore();
    });
  });
});
