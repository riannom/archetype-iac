import React from 'react';
import { act, render, screen } from '@testing-library/react';

import { usePersistedSet, usePersistedState } from './usePersistedState';

function StateConsumer({ storageKey }: { storageKey: string }) {
  const [value, setValue] = usePersistedState(storageKey, 0);
  return (
    <div>
      <span>value:{value}</span>
      <button onClick={() => setValue((prev) => prev + 1)}>inc</button>
    </div>
  );
}

function SetConsumer({ storageKey }: { storageKey: string }) {
  const [set, toggle, clear] = usePersistedSet(storageKey);
  return (
    <div>
      <span>size:{set.size}</span>
      <button onClick={() => toggle('a')}>toggle</button>
      <button onClick={clear}>clear</button>
    </div>
  );
}

describe('usePersistedState', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('loads initial value from localStorage and persists updates', () => {
    localStorage.setItem('count', JSON.stringify(5));

    render(<StateConsumer storageKey="count" />);
    expect(screen.getByText('value:5')).toBeInTheDocument();

    act(() => {
      screen.getByText('inc').click();
    });

    expect(screen.getByText('value:6')).toBeInTheDocument();
    expect(localStorage.getItem('count')).toBe(JSON.stringify(6));
  });

  it('handles persisted sets', () => {
    render(<SetConsumer storageKey="set" />);
    expect(screen.getByText('size:0')).toBeInTheDocument();

    act(() => {
      screen.getByText('toggle').click();
    });

    expect(screen.getByText('size:1')).toBeInTheDocument();
    expect(localStorage.getItem('set')).toBe(JSON.stringify(['a']));

    act(() => {
      screen.getByText('clear').click();
    });

    expect(screen.getByText('size:0')).toBeInTheDocument();
  });
});
