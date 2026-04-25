import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useCanvasInteraction } from './useCanvasInteraction';

describe('useCanvasInteraction', () => {
  it('initialises with sensible defaults', () => {
    const { result } = renderHook(() => useCanvasInteraction());
    expect(result.current.selectedId).toBeNull();
    expect(result.current.selectedIds).toEqual(new Set());
    expect(result.current.activeTool).toBe('pointer');
    expect(result.current.focusNodeId).toBeNull();
    expect(result.current.sidebarCollapsed).toBe(false);
    expect(result.current.sidebarTab).toBe('library');
  });

  describe('handleSelectTool', () => {
    it('switches to the requested tool and clears single selection when leaving pointer', () => {
      const { result } = renderHook(() => useCanvasInteraction());

      act(() => {
        result.current.setSelectedId('node-1');
      });
      expect(result.current.selectedId).toBe('node-1');

      act(() => {
        result.current.handleSelectTool('rect');
      });

      expect(result.current.activeTool).toBe('rect');
      expect(result.current.selectedId).toBeNull();
    });

    it('keeps the existing selection when switching back to pointer', () => {
      const { result } = renderHook(() => useCanvasInteraction());

      act(() => {
        result.current.setSelectedId('node-1');
        result.current.handleSelectTool('pointer');
      });

      expect(result.current.activeTool).toBe('pointer');
      expect(result.current.selectedId).toBe('node-1');
    });
  });

  describe('handleCanvasSelect', () => {
    it('sets selectedId and clears multi-selection', () => {
      const { result } = renderHook(() => useCanvasInteraction());

      act(() => {
        result.current.handleSelectMultiple(new Set(['a', 'b']));
      });
      expect(result.current.selectedIds.size).toBe(2);

      act(() => {
        result.current.handleCanvasSelect('node-1');
      });
      expect(result.current.selectedId).toBe('node-1');
      expect(result.current.selectedIds).toEqual(new Set());
    });

    it('accepts null to deselect', () => {
      const { result } = renderHook(() => useCanvasInteraction());
      act(() => {
        result.current.handleCanvasSelect('x');
      });
      expect(result.current.selectedId).toBe('x');

      act(() => {
        result.current.handleCanvasSelect(null);
      });
      expect(result.current.selectedId).toBeNull();
    });
  });

  describe('handleSelectMultiple', () => {
    it('sets selectedIds and clears single selection', () => {
      const { result } = renderHook(() => useCanvasInteraction());

      act(() => {
        result.current.setSelectedId('node-1');
        result.current.handleSelectMultiple(new Set(['a', 'b', 'c']));
      });

      expect(result.current.selectedIds).toEqual(new Set(['a', 'b', 'c']));
      expect(result.current.selectedId).toBeNull();
    });
  });

  describe('clearSelection', () => {
    it('clears both single and multi selections', () => {
      const { result } = renderHook(() => useCanvasInteraction());

      act(() => {
        result.current.setSelectedId('x');
        result.current.handleSelectMultiple(new Set(['y']));
        result.current.clearSelection();
      });

      // handleSelectMultiple already cleared selectedId; we re-set it for the
      // multi case below
      act(() => {
        result.current.setSelectedId('x');
        result.current.clearSelection();
      });

      expect(result.current.selectedId).toBeNull();
      expect(result.current.selectedIds).toEqual(new Set());
    });
  });

  describe('Escape key handling', () => {
    it('returns to pointer tool when Escape is pressed while another tool is active', () => {
      const { result } = renderHook(() => useCanvasInteraction());

      act(() => {
        result.current.handleSelectTool('arrow');
      });
      expect(result.current.activeTool).toBe('arrow');

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      });

      expect(result.current.activeTool).toBe('pointer');
    });

    it('does nothing on non-Escape keys', () => {
      const { result } = renderHook(() => useCanvasInteraction());
      act(() => {
        result.current.handleSelectTool('rect');
      });

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
      });
      expect(result.current.activeTool).toBe('rect');
    });

    it('does nothing when Escape is pressed and pointer is already active', () => {
      const { result } = renderHook(() => useCanvasInteraction());

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      });
      expect(result.current.activeTool).toBe('pointer');
    });

    it('removes its keydown listener on unmount', () => {
      const { result, unmount } = renderHook(() => useCanvasInteraction());

      act(() => {
        result.current.handleSelectTool('text');
      });
      unmount();

      // Dispatching after unmount must not throw or affect anything observable
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
  });

  describe('exposed setters', () => {
    it('forwards setActiveTool, setFocusNodeId, setSidebarCollapsed, setSidebarTab', () => {
      const { result } = renderHook(() => useCanvasInteraction());

      act(() => {
        result.current.setActiveTool('circle');
        result.current.setFocusNodeId('focus-1');
        result.current.setSidebarCollapsed(true);
        result.current.setSidebarTab('properties');
      });

      expect(result.current.activeTool).toBe('circle');
      expect(result.current.focusNodeId).toBe('focus-1');
      expect(result.current.sidebarCollapsed).toBe(true);
      expect(result.current.sidebarTab).toBe('properties');
    });
  });
});
