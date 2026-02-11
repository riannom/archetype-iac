// Theme System Type Definitions

/**
 * Color scale matching Tailwind shade levels (50-950)
 */
export interface ColorScale {
  50: string;
  100: string;
  200: string;
  300: string;
  400: string;
  500: string;
  600: string;
  700: string;
  800: string;
  900: string;
  950: string;
}

/**
 * Mode-specific (light/dark) color values
 */
export interface ThemeModeColors {
  bgBase: string;
  bgSurface: string;
  border: string;
  text: string;
  textMuted: string;
  accentPrimary: string;
  accentHover: string;
  canvasGrid: string;
  nodeGlow: string;
  scrollbarThumb: string;
}

/**
 * Complete theme definition
 */
export interface Theme {
  id: string;
  name: string;
  description?: string;
  colors: {
    accent: ColorScale;    // Primary accent color (e.g., sage green, ocean blue)
    neutral: ColorScale;   // Background/neutral colors (e.g., stone, slate)
    success: string;
    warning: string;
    error: string;
    info: string;
  };
  light: ThemeModeColors;
  dark: ThemeModeColors;
}

/**
 * User theme preferences stored in localStorage
 */
export interface ThemePreferences {
  themeId: string;
  mode: 'light' | 'dark' | 'system';
  backgroundId: string;
  backgroundOpacity: number; // 0-100
  taskLogOpacity: number; // 0-100
  favoriteBackgrounds?: string[];
}

/**
 * Theme context value exposed to components
 */
export interface ThemeContextValue {
  theme: Theme;
  backgroundId: string;
  backgroundOpacity: number;
  taskLogOpacity: number;
  mode: 'light' | 'dark';
  effectiveMode: 'light' | 'dark';
  preferences: ThemePreferences;
  availableThemes: Theme[];
  availableBackgrounds: {
    id: string;
    name: string;
    animated: boolean;
    categories: string[];
    preferredMode?: 'light' | 'dark' | 'both';
  }[];
  setTheme: (themeId: string) => void;
  setBackground: (backgroundId: string) => void;
  setBackgroundOpacity: (opacity: number) => void;
  setTaskLogOpacity: (opacity: number) => void;
  toggleFavoriteBackground: (backgroundId: string) => void;
  setMode: (mode: 'light' | 'dark' | 'system') => void;
  toggleMode: () => void;
  importTheme: (themeJson: string) => Theme | null;
  exportTheme: (themeId: string) => string | null;
  removeCustomTheme: (themeId: string) => boolean;
}
