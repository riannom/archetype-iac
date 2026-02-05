import { vi } from 'vitest';

vi.mock('../components/backgrounds/AnimatedBackground', () => ({
  ANIMATED_PATTERNS: ['breath', 'constellation'],
}));

import {
  backgroundPatterns,
  filterBackgroundsByCategory,
  getBackgroundById,
  isAnimatedBackgroundId,
  isBackgroundPreferredInMode,
} from './backgrounds';

describe('backgrounds', () => {
  it('returns patterns by id and category', () => {
    expect(getBackgroundById('minimal')?.name).toBe('Minimal');
    expect(filterBackgroundsByCategory('minimal').length).toBeGreaterThan(0);
  });

  it('recognizes animated backgrounds', () => {
    expect(isAnimatedBackgroundId('breath')).toBe(true);
    expect(isAnimatedBackgroundId('minimal')).toBe(false);
  });

  it('handles preferred mode checks', () => {
    const sample = backgroundPatterns.find((pattern) => pattern.preferredMode === 'dark');
    if (!sample) {
      return;
    }
    expect(isBackgroundPreferredInMode(sample.id, 'dark')).toBe(true);
  });
});
