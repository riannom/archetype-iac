import { builtInThemes, DEFAULT_THEME_ID, getBuiltInTheme } from './index';

describe('theme index exports', () => {
  it('exposes built-in themes and lookup', () => {
    expect(builtInThemes.length).toBeGreaterThan(0);
    expect(getBuiltInTheme(DEFAULT_THEME_ID)).toBeDefined();
  });
});
