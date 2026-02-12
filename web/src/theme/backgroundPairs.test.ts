import { getSuggestedBackgroundForTheme } from './backgroundPairs';

describe('backgroundPairs', () => {
  it('returns a suggested background or default', () => {
    expect(getSuggestedBackgroundForTheme('sage-stone')).toBe('breath');
    expect(getSuggestedBackgroundForTheme('unknown')).toBe('minimal');
  });

  it('returns mode-specific suggestions when configured', () => {
    expect(getSuggestedBackgroundForTheme('sunrise', 'light')).toBe('sunset-sailing');
    expect(getSuggestedBackgroundForTheme('sunrise', 'dark')).toBe('moonlit-clouds');
  });

  it('keeps suggestions mode-compatible', () => {
    expect(getSuggestedBackgroundForTheme('copper', 'dark')).toBe('embers-rising');
    expect(getSuggestedBackgroundForTheme('copper', 'light')).toBe('autumn-wind');
  });
});
