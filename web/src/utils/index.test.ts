import { formatSize, getRuntimeStatusColor } from './index';

describe('utils index exports', () => {
  it('re-exports utility helpers', () => {
    expect(formatSize(1024 * 1024)).toBe('1 MB');
    expect(getRuntimeStatusColor('running')).toContain('green');
  });
});
