import { describe, it, expect } from 'vitest';
import * as uiBarrel from './index';

describe('components/ui barrel', () => {
  it('re-exports the documented Modal surface', () => {
    expect(typeof uiBarrel.Modal).toBe('function');
    expect(typeof uiBarrel.ModalHeader).toBe('function');
    expect(typeof uiBarrel.ModalFooter).toBe('function');
  });

  it('re-exports Toast and ToastContainer', () => {
    expect(typeof uiBarrel.Toast).toBe('function');
    expect(typeof uiBarrel.ToastContainer).toBe('function');
  });
});
