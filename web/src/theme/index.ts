// Theme System - Public API

// Types
export type {
  ColorScale,
  ThemeModeColors,
  Theme,
  ThemePreferences,
  ThemeContextValue,
} from './types';

// Presets
export {
  builtInThemes,
  sageStoneTheme,
  oceanTheme,
  copperTheme,
  violetTheme,
  roseTheme,
  serenityTheme,
  forestTheme,
  cyberTheme,
  sunriseTheme,
  sunsetTheme,
  sakuraYoshinoTheme,
  sakuraSumieTheme,
  midnightTheme,
  desertTheme,
  seasonalTheme,
  DEFAULT_THEME_ID,
  getBuiltInTheme,
} from './presets';

// Provider & Hook
export { ThemeProvider, useTheme } from './ThemeProvider';

// Components
export { ThemeSelector } from './ThemeSelector';
