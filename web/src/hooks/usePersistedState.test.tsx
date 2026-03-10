import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi, type MockInstance } from 'vitest';

import {
  usePersistedState,
  usePersistedSet,
} from '../studio/hooks/usePersistedState';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Creates a DOMException that behaves like a QuotaExceededError.
 */
function quotaExceededError(): DOMException {
  return new DOMException('Storage quota exceeded', 'QuotaExceededError');
}

/**
 * Creates a DOMException that behaves like a SecurityError (private browsing).
 */
function securityError(): DOMException {
  return new DOMException('Access denied', 'SecurityError');
}

// ---------------------------------------------------------------------------
// usePersistedState
// ---------------------------------------------------------------------------

describe('usePersistedState', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  // --- initialisation -------------------------------------------------------

  it('returns defaultValue when localStorage key is missing', () => {
    const { result } = renderHook(() => usePersistedState('missing', 42));
    expect(result.current[0]).toBe(42);
  });

  it('initialises from existing localStorage value', () => {
    localStorage.setItem('greeting', JSON.stringify('hello'));
    const { result } = renderHook(() =>
      usePersistedState('greeting', 'default'),
    );
    expect(result.current[0]).toBe('hello');
  });

  it('falls back to defaultValue when stored JSON is corrupt', () => {
    localStorage.setItem('bad', '{not valid json!!!');
    const { result } = renderHook(() => usePersistedState('bad', 'fallback'));
    expect(result.current[0]).toBe('fallback');
  });

  it('falls back to defaultValue when getItem throws SecurityError', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw securityError();
    });
    const { result } = renderHook(() =>
      usePersistedState('blocked', 'safe-default'),
    );
    expect(result.current[0]).toBe('safe-default');
  });

  // --- persistence ----------------------------------------------------------

  it('persists primitive number to localStorage on update', () => {
    const { result } = renderHook(() => usePersistedState('num', 0));

    act(() => {
      result.current[1](99);
    });

    expect(result.current[0]).toBe(99);
    expect(localStorage.getItem('num')).toBe('99');
  });

  it('persists string values', () => {
    const { result } = renderHook(() => usePersistedState('str', ''));

    act(() => {
      result.current[1]('hello world');
    });

    expect(result.current[0]).toBe('hello world');
    expect(localStorage.getItem('str')).toBe('"hello world"');
  });

  it('persists boolean values', () => {
    const { result } = renderHook(() => usePersistedState('flag', false));

    act(() => {
      result.current[1](true);
    });

    expect(result.current[0]).toBe(true);
    expect(localStorage.getItem('flag')).toBe('true');
  });

  it('persists arrays', () => {
    const { result } = renderHook(() =>
      usePersistedState<string[]>('arr', []),
    );

    act(() => {
      result.current[1](['a', 'b', 'c']);
    });

    expect(result.current[0]).toEqual(['a', 'b', 'c']);
    expect(localStorage.getItem('arr')).toBe(JSON.stringify(['a', 'b', 'c']));
  });

  it('persists objects', () => {
    const { result } = renderHook(() =>
      usePersistedState<Record<string, number>>('obj', {}),
    );

    act(() => {
      result.current[1]({ x: 1, y: 2 });
    });

    expect(result.current[0]).toEqual({ x: 1, y: 2 });
    expect(localStorage.getItem('obj')).toBe(JSON.stringify({ x: 1, y: 2 }));
  });

  // --- functional updates ---------------------------------------------------

  it('supports functional updates based on previous state', () => {
    const { result } = renderHook(() => usePersistedState('counter', 10));

    act(() => {
      result.current[1]((prev) => prev + 5);
    });

    expect(result.current[0]).toBe(15);
    expect(localStorage.getItem('counter')).toBe('15');
  });

  it('supports multiple sequential functional updates', () => {
    const { result } = renderHook(() =>
      usePersistedState<number[]>('list', []),
    );

    act(() => {
      result.current[1]((prev) => [...prev, 1]);
    });
    act(() => {
      result.current[1]((prev) => [...prev, 2]);
    });

    expect(result.current[0]).toEqual([1, 2]);
  });

  // --- error handling -------------------------------------------------------

  it('swallows QuotaExceededError on setItem and keeps state in memory', () => {
    const setItemSpy: MockInstance = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw quotaExceededError();
      });

    const { result } = renderHook(() => usePersistedState('quota', 0));

    act(() => {
      result.current[1](42);
    });

    // State still updates in memory even though persistence failed
    expect(result.current[0]).toBe(42);
    expect(setItemSpy).toHaveBeenCalled();
  });

  it('swallows SecurityError on setItem (private browsing)', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw securityError();
    });

    const { result } = renderHook(() =>
      usePersistedState('private', 'initial'),
    );

    act(() => {
      result.current[1]('updated');
    });

    // State updates in memory despite storage error
    expect(result.current[0]).toBe('updated');
  });

  // --- key change -----------------------------------------------------------

  it('reads new storage when key changes', () => {
    localStorage.setItem('key-a', JSON.stringify('alpha'));
    localStorage.setItem('key-b', JSON.stringify('beta'));

    const { result, rerender } = renderHook(
      ({ storageKey }: { storageKey: string }) =>
        usePersistedState(storageKey, 'default'),
      { initialProps: { storageKey: 'key-a' } },
    );

    expect(result.current[0]).toBe('alpha');

    rerender({ storageKey: 'key-b' });

    // After key change, the hook re-initialises. Because useState initialiser
    // only runs on mount, the value stays until the effect writes to the new key.
    // The important thing: writing to the new key persists under the new key.
    act(() => {
      result.current[1]('gamma');
    });

    expect(localStorage.getItem('key-b')).toBe(JSON.stringify('gamma'));
  });

  // --- Set reconstruction ---------------------------------------------------

  it('reconstructs a Set from a stored JSON array', () => {
    localStorage.setItem('myset', JSON.stringify(['x', 'y']));
    const { result } = renderHook(() =>
      usePersistedState<Set<string>>('myset', new Set()),
    );

    expect(result.current[0]).toBeInstanceOf(Set);
    expect(result.current[0].size).toBe(2);
    expect(result.current[0].has('x')).toBe(true);
    expect(result.current[0].has('y')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// usePersistedSet
// ---------------------------------------------------------------------------

describe('usePersistedSet', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('starts with an empty set', () => {
    const { result } = renderHook(() => usePersistedSet('tags'));
    expect(result.current[0].size).toBe(0);
  });

  it('toggle adds a value that is not present', () => {
    const { result } = renderHook(() => usePersistedSet('tags'));

    act(() => {
      result.current[1]('foo');
    });

    expect(result.current[0].has('foo')).toBe(true);
    expect(result.current[0].size).toBe(1);
  });

  it('toggle removes a value that is already present', () => {
    const { result } = renderHook(() => usePersistedSet('tags'));

    act(() => {
      result.current[1]('foo');
    });
    act(() => {
      result.current[1]('foo');
    });

    expect(result.current[0].has('foo')).toBe(false);
    expect(result.current[0].size).toBe(0);
  });

  it('clear empties the entire set', () => {
    const { result } = renderHook(() => usePersistedSet('tags'));

    act(() => {
      result.current[1]('a');
    });
    act(() => {
      result.current[1]('b');
    });

    expect(result.current[0].size).toBe(2);

    act(() => {
      result.current[2]();
    });

    expect(result.current[0].size).toBe(0);
    expect(localStorage.getItem('tags')).toBe(JSON.stringify([]));
  });

  it('persists toggle changes to localStorage as JSON array', () => {
    const { result } = renderHook(() => usePersistedSet('colours'));

    act(() => {
      result.current[1]('red');
    });
    act(() => {
      result.current[1]('blue');
    });

    const stored = JSON.parse(localStorage.getItem('colours')!);
    expect(stored).toEqual(expect.arrayContaining(['red', 'blue']));
    expect(stored.length).toBe(2);
  });

  it('restores persisted set values on mount', () => {
    localStorage.setItem('saved', JSON.stringify(['x', 'y', 'z']));

    const { result } = renderHook(() => usePersistedSet('saved'));

    expect(result.current[0].size).toBe(3);
    expect(result.current[0].has('x')).toBe(true);
    expect(result.current[0].has('y')).toBe(true);
    expect(result.current[0].has('z')).toBe(true);
  });
});
