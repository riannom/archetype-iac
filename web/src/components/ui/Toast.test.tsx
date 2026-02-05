import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import { Toast } from './Toast';

describe('Toast', () => {
  it('calls onDismiss when close button is clicked', () => {
    const onDismiss = vi.fn();
    render(
      <Toast level="info" title="Hello" message="World" onDismiss={onDismiss} />
    );

    fireEvent.click(screen.getByRole('button'));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
