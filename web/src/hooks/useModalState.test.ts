import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useModalState } from './useModalState';

describe('useModalState', () => {
  it('initialises closed with null data', () => {
    const { result } = renderHook(() => useModalState());
    expect(result.current.isOpen).toBe(false);
    expect(result.current.data).toBeNull();
  });

  describe('void variant (no data)', () => {
    it('opens and closes without arguments', () => {
      const { result } = renderHook(() => useModalState());

      act(() => {
        result.current.open();
      });
      expect(result.current.isOpen).toBe(true);
      expect(result.current.data).toBeNull();

      act(() => {
        result.current.close();
      });
      expect(result.current.isOpen).toBe(false);
      expect(result.current.data).toBeNull();
    });
  });

  describe('typed variant (with data)', () => {
    interface Host {
      id: string;
      name: string;
    }

    it('stores data on open and clears it on close', () => {
      const { result } = renderHook(() => useModalState<Host>());
      const host: Host = { id: 'h1', name: 'agent-01' };

      act(() => {
        result.current.open(host);
      });
      expect(result.current.isOpen).toBe(true);
      expect(result.current.data).toEqual(host);

      act(() => {
        result.current.close();
      });
      expect(result.current.isOpen).toBe(false);
      expect(result.current.data).toBeNull();
    });

    it('replaces stored data when reopened with a new value', () => {
      const { result } = renderHook(() => useModalState<Host>());

      act(() => {
        result.current.open({ id: 'h1', name: 'agent-01' });
      });
      expect(result.current.data).toEqual({ id: 'h1', name: 'agent-01' });

      act(() => {
        result.current.open({ id: 'h2', name: 'agent-02' });
      });
      expect(result.current.isOpen).toBe(true);
      expect(result.current.data).toEqual({ id: 'h2', name: 'agent-02' });
    });

    it('preserves prior data when reopened without a value', () => {
      // The hook intentionally only overwrites data when the caller passes a
      // defined value — calling open() without an argument keeps the existing
      // data so consumers can reopen the same modal cheaply.
      const { result } = renderHook(() => useModalState<Host>());

      act(() => {
        result.current.open({ id: 'h1', name: 'agent-01' });
      });
      act(() => {
        result.current.close();
      });
      expect(result.current.data).toBeNull();

      act(() => {
        result.current.open({ id: 'h1', name: 'agent-01' });
      });
      act(() => {
        // simulating the "reopen without re-supplying data" pattern by
        // calling the underlying setter — `open()` with no args is allowed
        // by the void variant only, so we cast through the typed signature.
        (result.current.open as (v?: Host) => void)();
      });
      expect(result.current.isOpen).toBe(true);
      expect(result.current.data).toEqual({ id: 'h1', name: 'agent-01' });
    });
  });

  it('keeps open and close referentially stable across renders', () => {
    const { result, rerender } = renderHook(() => useModalState());
    const firstOpen = result.current.open;
    const firstClose = result.current.close;

    rerender();
    expect(result.current.open).toBe(firstOpen);
    expect(result.current.close).toBe(firstClose);
  });
});
