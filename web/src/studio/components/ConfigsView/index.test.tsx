import { describe, it, expect } from 'vitest';
import * as exports from './index';

describe('ConfigsView exports', () => {
  it('exports components', () => {
    expect(exports).toBeTruthy();
  });
});
