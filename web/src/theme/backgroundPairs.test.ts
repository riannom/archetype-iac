import { getSuggestedBackgroundForTheme, THEME_BACKGROUND_SUGGESTIONS } from './backgroundPairs';

describe('backgroundPairs', () => {
  it('returns a suggested background or default', () => {
    expect(getSuggestedBackgroundForTheme('sage-stone')).toBe(
      THEME_BACKGROUND_SUGGESTIONS['sage-stone']
    );
    expect(getSuggestedBackgroundForTheme('unknown')).toBe('minimal');
  });
});
