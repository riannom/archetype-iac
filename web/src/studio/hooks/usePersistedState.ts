import { useState, useEffect, useCallback } from 'react';

/**
 * A hook that persists state to localStorage.
 * Supports primitive values, arrays, objects, and Sets (serialized as arrays).
 */
export function usePersistedState<T>(
  key: string,
  defaultValue: T
): [T, (value: T | ((prev: T) => T)) => void] {
  // Initialize state from localStorage or default
  const [state, setState] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(key);
      if (stored === null) {
        return defaultValue;
      }
      const parsed = JSON.parse(stored);
      // Handle Set reconstruction
      if (defaultValue instanceof Set) {
        return new Set(parsed) as T;
      }
      return parsed;
    } catch {
      return defaultValue;
    }
  });

  // Persist to localStorage whenever state changes
  useEffect(() => {
    try {
      // Handle Set serialization
      if (state instanceof Set) {
        localStorage.setItem(key, JSON.stringify(Array.from(state)));
      } else {
        localStorage.setItem(key, JSON.stringify(state));
      }
    } catch {
      // localStorage might be full or disabled
    }
  }, [key, state]);

  // Wrapper to handle Set updates properly
  const setPersistedState = useCallback((value: T | ((prev: T) => T)) => {
    setState((prev) => {
      const newValue = typeof value === 'function' ? (value as (prev: T) => T)(prev) : value;
      return newValue;
    });
  }, []);

  return [state, setPersistedState];
}

/**
 * A hook specifically for persisted Set<string> state.
 * Provides convenient toggle and clear methods.
 */
export function usePersistedSet(
  key: string
): [Set<string>, (value: string) => void, () => void] {
  const [set, setSet] = usePersistedState<Set<string>>(key, new Set());

  const toggle = useCallback((value: string) => {
    setSet((prev) => {
      const next = new Set(prev);
      if (next.has(value)) {
        next.delete(value);
      } else {
        next.add(value);
      }
      return next;
    });
  }, [setSet]);

  const clear = useCallback(() => {
    setSet(new Set());
  }, [setSet]);

  return [set, toggle, clear];
}
