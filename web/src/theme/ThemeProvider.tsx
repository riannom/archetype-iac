import React, { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef } from 'react';
import type { Theme, ThemePreferences, ThemeContextValue } from './types';
import { builtInThemes, DEFAULT_THEME_ID, getBuiltInTheme } from './presets';
import {
  backgroundPatterns,
  getBackgroundById,
  isAnimatedBackgroundId,
} from './backgrounds';
import { getSuggestedBackgroundForTheme } from './backgroundPairs';
import { AnimatedBackground } from '../components/backgrounds/AnimatedBackground';
import { API_BASE_URL } from '../api';

// Storage keys
const PREFS_KEY = 'archetype_theme_prefs';
const CUSTOM_THEMES_KEY = 'archetype_custom_themes';

function resolvePreferenceMode(mode: unknown): 'light' | 'dark' {
  if (mode === 'light' || mode === 'dark') {
    return mode;
  }
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return 'light';
}

// Default preferences
const defaultPreferences: ThemePreferences = {
  themeId: DEFAULT_THEME_ID,
  mode: 'system',
  backgroundId: getSuggestedBackgroundForTheme(DEFAULT_THEME_ID, 'light'),
  backgroundOpacity: 50,
  taskLogOpacity: 92,
  favoriteBackgrounds: [],
  favoriteThemeIds: [],
  customThemes: [],
};

// Create context
const ThemeContext = createContext<ThemeContextValue | null>(null);

/**
 * Load preferences from localStorage
 */
function loadPreferences(): ThemePreferences {
  try {
    const stored = localStorage.getItem(PREFS_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      const parsedThemeId = parsed.themeId || DEFAULT_THEME_ID;
      const parsedMode = parsed.mode || 'system';
      const suggestedMode = resolvePreferenceMode(parsedMode);
      return {
        themeId: parsedThemeId,
        mode: parsedMode,
        backgroundId: parsed.backgroundId || getSuggestedBackgroundForTheme(parsedThemeId, suggestedMode),
        backgroundOpacity: typeof parsed.backgroundOpacity === 'number' ? parsed.backgroundOpacity : 50,
        taskLogOpacity: typeof parsed.taskLogOpacity === 'number' ? parsed.taskLogOpacity : 92,
        favoriteBackgrounds: Array.isArray(parsed.favoriteBackgrounds) ? parsed.favoriteBackgrounds : [],
        favoriteThemeIds: Array.isArray(parsed.favoriteThemeIds) ? parsed.favoriteThemeIds : [],
        customThemes: Array.isArray(parsed.customThemes) ? parsed.customThemes.filter(validateTheme) : [],
      };
    }
  } catch (e) {
    console.warn('Failed to load theme preferences:', e);
  }
  return defaultPreferences;
}

function normalizePreferences(raw: unknown): ThemePreferences {
  if (!raw || typeof raw !== 'object') {
    return defaultPreferences;
  }
  const parsed = raw as Record<string, unknown>;
  const themeId = typeof parsed.themeId === 'string' ? parsed.themeId : DEFAULT_THEME_ID;
  const mode = parsed.mode === 'light' || parsed.mode === 'dark' || parsed.mode === 'system' ? parsed.mode : 'system';
  const suggestionMode = resolvePreferenceMode(mode);
  const backgroundId = typeof parsed.backgroundId === 'string'
    ? parsed.backgroundId
    : getSuggestedBackgroundForTheme(themeId, suggestionMode);
  return {
    themeId,
    mode,
    backgroundId: getBackgroundById(backgroundId) ? backgroundId : getSuggestedBackgroundForTheme(themeId, suggestionMode),
    backgroundOpacity:
      typeof parsed.backgroundOpacity === 'number' ? Math.max(0, Math.min(100, parsed.backgroundOpacity)) : 50,
    taskLogOpacity:
      typeof parsed.taskLogOpacity === 'number' ? Math.max(0, Math.min(100, parsed.taskLogOpacity)) : 92,
    favoriteBackgrounds: Array.isArray(parsed.favoriteBackgrounds)
      ? parsed.favoriteBackgrounds.filter((id): id is string => typeof id === 'string')
      : [],
    favoriteThemeIds: Array.isArray(parsed.favoriteThemeIds)
      ? parsed.favoriteThemeIds.filter((id): id is string => typeof id === 'string')
      : [],
    customThemes: Array.isArray(parsed.customThemes) ? parsed.customThemes.filter(validateTheme) : [],
  };
}

/**
 * Save preferences to localStorage
 */
function savePreferences(prefs: ThemePreferences): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch (e) {
    console.warn('Failed to save theme preferences:', e);
  }
}

/**
 * Load custom themes from localStorage
 */
function loadCustomThemes(): Theme[] {
  try {
    const stored = localStorage.getItem(CUSTOM_THEMES_KEY);
    if (stored) {
      return JSON.parse(stored);
    }
  } catch (e) {
    console.warn('Failed to load custom themes:', e);
  }
  return [];
}

/**
 * Save custom themes to localStorage
 */
function saveCustomThemes(themes: Theme[]): void {
  try {
    localStorage.setItem(CUSTOM_THEMES_KEY, JSON.stringify(themes));
  } catch (e) {
    console.warn('Failed to save custom themes:', e);
  }
}

/**
 * Apply theme colors to DOM via CSS custom properties
 */
function hexToRgbChannels(hex: string): string | null {
  const sanitized = hex.replace('#', '').trim();
  if (sanitized.length === 3) {
    const r = parseInt(sanitized[0] + sanitized[0], 16);
    const g = parseInt(sanitized[1] + sanitized[1], 16);
    const b = parseInt(sanitized[2] + sanitized[2], 16);
    return `${r} ${g} ${b}`;
  }
  if (sanitized.length === 6) {
    const r = parseInt(sanitized.slice(0, 2), 16);
    const g = parseInt(sanitized.slice(2, 4), 16);
    const b = parseInt(sanitized.slice(4, 6), 16);
    return `${r} ${g} ${b}`;
  }
  return null;
}

function applyThemeToDOM(theme: Theme, effectiveMode: 'light' | 'dark', taskLogOpacity: number): void {
  const root = document.documentElement;

  // Apply dark class for Tailwind
  root.classList.toggle('dark', effectiveMode === 'dark');

  // Inject accent color scale as CSS variables
  Object.entries(theme.colors.accent).forEach(([shade, value]) => {
    root.style.setProperty(`--color-accent-${shade}`, value);
  });

  // Inject neutral color scale as CSS variables
  Object.entries(theme.colors.neutral).forEach(([shade, value]) => {
    root.style.setProperty(`--color-neutral-${shade}`, value);
  });

  // Inject semantic colors
  root.style.setProperty('--color-success', theme.colors.success);
  root.style.setProperty('--color-warning', theme.colors.warning);
  root.style.setProperty('--color-error', theme.colors.error);
  root.style.setProperty('--color-info', theme.colors.info);

  // Inject mode-specific colors
  const modeColors = effectiveMode === 'dark' ? theme.dark : theme.light;
  root.style.setProperty('--color-bg-base', modeColors.bgBase);
  root.style.setProperty('--color-bg-surface', modeColors.bgSurface);
  root.style.setProperty('--color-border', modeColors.border);
  root.style.setProperty('--color-text', modeColors.text);
  root.style.setProperty('--color-text-muted', modeColors.textMuted);
  root.style.setProperty('--color-accent-primary', modeColors.accentPrimary);
  root.style.setProperty('--color-accent-hover', modeColors.accentHover);
  root.style.setProperty('--color-canvas-grid', modeColors.canvasGrid);
  root.style.setProperty('--color-node-glow', modeColors.nodeGlow);
  root.style.setProperty('--color-scrollbar-thumb', modeColors.scrollbarThumb);

  const taskLogBase = effectiveMode === 'dark' ? '#0c0a09' : '#f8fafc';
  const surfaceChannels = hexToRgbChannels(taskLogBase) || '255 255 255';
  const clampedOpacity = (Math.max(0, Math.min(100, taskLogOpacity)) / 100) * 0.95;
  root.style.setProperty('--tasklog-panel-bg', surfaceChannels);
  root.style.setProperty('--tasklog-opacity', clampedOpacity.toFixed(2));
}

function applyBackgroundToDOM(backgroundId: string, opacity: number): void {
  const body = document.body;
  const clampedOpacity = Math.max(0, Math.min(100, opacity));
  const sanitizedBackgroundId = getBackgroundById(backgroundId) ? backgroundId : 'minimal';

  body.className = body.className
    .split(' ')
    .filter(cls => !cls.startsWith('bg-pattern-'))
    .join(' ');
  body.classList.add(`bg-pattern-${sanitizedBackgroundId}`);

  document.documentElement.style.setProperty('--background-opacity', String(clampedOpacity));
}

/**
 * Validate imported theme structure
 */
function validateTheme(obj: unknown): obj is Theme {
  if (!obj || typeof obj !== 'object') return false;
  const t = obj as Record<string, unknown>;

  if (typeof t.id !== 'string' || !t.id) return false;
  if (typeof t.name !== 'string' || !t.name) return false;
  if (!t.colors || typeof t.colors !== 'object') return false;
  if (!t.light || typeof t.light !== 'object') return false;
  if (!t.dark || typeof t.dark !== 'object') return false;

  const colors = t.colors as Record<string, unknown>;
  if (!colors.accent || typeof colors.accent !== 'object') return false;
  if (!colors.neutral || typeof colors.neutral !== 'object') return false;

  // Check that accent has required shade keys
  const accent = colors.accent as Record<string, unknown>;
  const requiredShades = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950'];
  for (const shade of requiredShades) {
    if (typeof accent[shade] !== 'string') return false;
  }

  return true;
}

interface ThemeProviderProps {
  children: React.ReactNode;
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  const [preferences, setPreferences] = useState<ThemePreferences>(() => loadPreferences());
  const [customThemes, setCustomThemes] = useState<Theme[]>(() => {
    const fromPrefs = preferences.customThemes;
    if (Array.isArray(fromPrefs) && fromPrefs.length > 0) {
      return fromPrefs;
    }
    return loadCustomThemes();
  });
  const [systemDark, setSystemDark] = useState<boolean>(
    window.matchMedia('(prefers-color-scheme: dark)').matches
  );
  const [serverSyncReady, setServerSyncReady] = useState(false);
  const [serverSyncEnabled, setServerSyncEnabled] = useState(false);
  const syncTimeoutRef = useRef<number | null>(null);

  // All available themes (built-in + custom)
  const availableThemes = useMemo(() => {
    return [...builtInThemes, ...customThemes];
  }, [customThemes]);

  // Current theme object
  const theme = useMemo(() => {
    const found = availableThemes.find(t => t.id === preferences.themeId);
    return found || getBuiltInTheme(DEFAULT_THEME_ID)!;
  }, [availableThemes, preferences.themeId]);
  const selectedBackground = useMemo(() => {
    return getBackgroundById(preferences.backgroundId) || getBackgroundById('minimal')!;
  }, [preferences.backgroundId]);

  // Effective mode (resolves 'system' to actual mode)
  const effectiveMode = useMemo(() => {
    if (preferences.mode === 'system') {
      return systemDark ? 'dark' : 'light';
    }
    return preferences.mode;
  }, [preferences.mode, systemDark]);

  // Listen for system dark mode changes
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      setSystemDark(e.matches);
    };
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  // Hydrate theme settings from API for authenticated users.
  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      setServerSyncEnabled(false);
      setServerSyncReady(true);
      return;
    }

    let cancelled = false;
    setServerSyncEnabled(true);
    const hydratePreferences = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/auth/preferences`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) {
          return;
        }

        const data = await res.json();
        const normalized = normalizePreferences(data?.theme_settings);
        if (!cancelled) {
          setPreferences((prev) => ({ ...prev, ...normalized }));
          setCustomThemes(normalized.customThemes || []);
        }
      } catch (error) {
        console.warn('Failed to load theme settings from API:', error);
      } finally {
        if (!cancelled) {
          setServerSyncReady(true);
        }
      }
    };

    hydratePreferences();
    return () => {
      cancelled = true;
    };
  }, []);

  // Apply theme to DOM whenever theme or mode changes
  useEffect(() => {
    applyThemeToDOM(theme, effectiveMode, preferences.taskLogOpacity);
  }, [theme, effectiveMode, preferences.taskLogOpacity]);

  useEffect(() => {
    applyBackgroundToDOM(selectedBackground.id, preferences.backgroundOpacity);
  }, [selectedBackground.id, preferences.backgroundOpacity]);

  // Save preferences whenever they change
  useEffect(() => {
    savePreferences({ ...preferences, customThemes });
  }, [preferences, customThemes]);

  // Save custom themes whenever they change
  useEffect(() => {
    saveCustomThemes(customThemes);
  }, [customThemes]);

  // Persist theme settings to API whenever they change for authenticated users.
  useEffect(() => {
    if (!serverSyncEnabled || !serverSyncReady) {
      return;
    }
    const token = localStorage.getItem('token');
    if (!token) {
      return;
    }

    if (syncTimeoutRef.current) {
      window.clearTimeout(syncTimeoutRef.current);
    }
    syncTimeoutRef.current = window.setTimeout(async () => {
      try {
        await fetch(`${API_BASE_URL}/auth/preferences`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            theme_settings: {
              ...preferences,
              customThemes,
            },
          }),
        });
      } catch (error) {
        console.warn('Failed to persist theme settings to API:', error);
      }
    }, 300);

    return () => {
      if (syncTimeoutRef.current) {
        window.clearTimeout(syncTimeoutRef.current);
      }
    };
  }, [preferences, customThemes, serverSyncEnabled, serverSyncReady]);

  // Set theme by ID
  const setTheme = useCallback((themeId: string) => {
    setPreferences(prev => {
      const previousSuggested = getSuggestedBackgroundForTheme(prev.themeId, effectiveMode);
      const nextSuggested = getSuggestedBackgroundForTheme(themeId, effectiveMode);
      const shouldFollowSuggestion = prev.backgroundId === 'minimal' || prev.backgroundId === previousSuggested;

      return {
        ...prev,
        themeId,
        backgroundId: shouldFollowSuggestion ? nextSuggested : prev.backgroundId,
      };
    });
  }, [effectiveMode]);

  const setBackground = useCallback((backgroundId: string) => {
    const exists = getBackgroundById(backgroundId);
    setPreferences(prev => ({ ...prev, backgroundId: exists ? backgroundId : 'minimal' }));
  }, []);

  const setBackgroundOpacity = useCallback((opacity: number) => {
    const clampedOpacity = Math.max(0, Math.min(100, opacity));
    setPreferences(prev => ({ ...prev, backgroundOpacity: clampedOpacity }));
  }, []);

  const setTaskLogOpacity = useCallback((opacity: number) => {
    const clampedOpacity = Math.max(0, Math.min(100, opacity));
    setPreferences(prev => ({ ...prev, taskLogOpacity: clampedOpacity }));
  }, []);

  const toggleFavoriteBackground = useCallback((backgroundId: string) => {
    setPreferences(prev => {
      const favorites = prev.favoriteBackgrounds || [];
      const nextFavorites = favorites.includes(backgroundId)
        ? favorites.filter(id => id !== backgroundId)
        : [...favorites, backgroundId];

      return {
        ...prev,
        favoriteBackgrounds: nextFavorites,
      };
    });
  }, []);

  const toggleFavoriteTheme = useCallback((themeId: string) => {
    setPreferences(prev => {
      const favorites = prev.favoriteThemeIds || [];
      const nextFavorites = favorites.includes(themeId)
        ? favorites.filter(id => id !== themeId)
        : [...favorites, themeId];

      return {
        ...prev,
        favoriteThemeIds: nextFavorites,
      };
    });
  }, []);

  // Set mode (light/dark/system)
  const setMode = useCallback((mode: 'light' | 'dark' | 'system') => {
    setPreferences(prev => {
      const currentMode = prev.mode === 'system' ? effectiveMode : prev.mode;
      const nextMode = mode === 'system'
        ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
        : mode;
      const currentSuggested = getSuggestedBackgroundForTheme(prev.themeId, currentMode);
      const nextSuggested = getSuggestedBackgroundForTheme(prev.themeId, nextMode);
      const shouldFollowSuggestion = prev.backgroundId === 'minimal' || prev.backgroundId === currentSuggested;
      return {
        ...prev,
        mode,
        backgroundId: shouldFollowSuggestion ? nextSuggested : prev.backgroundId,
      };
    });
  }, [effectiveMode]);

  // Toggle between light and dark (ignores system)
  const toggleMode = useCallback(() => {
    setPreferences(prev => {
      const currentMode = prev.mode === 'system' ? effectiveMode : prev.mode;
      const nextMode = prev.mode === 'dark' || (prev.mode === 'system' && systemDark) ? 'light' : 'dark';
      const currentSuggested = getSuggestedBackgroundForTheme(prev.themeId, currentMode);
      const nextSuggested = getSuggestedBackgroundForTheme(prev.themeId, nextMode);
      const shouldFollowSuggestion = prev.backgroundId === 'minimal' || prev.backgroundId === currentSuggested;
      return {
        ...prev,
        mode: nextMode,
        backgroundId: shouldFollowSuggestion ? nextSuggested : prev.backgroundId,
      };
    });
  }, [effectiveMode, systemDark]);

  // Import a custom theme from JSON string
  const importTheme = useCallback((themeJson: string): Theme | null => {
    try {
      const parsed = JSON.parse(themeJson);
      if (!validateTheme(parsed)) {
        console.error('Invalid theme structure');
        return null;
      }

      // Check for ID collision with built-in themes
      if (builtInThemes.some(t => t.id === parsed.id)) {
        parsed.id = `custom-${parsed.id}-${Date.now()}`;
      }

      // Check for ID collision with existing custom themes
      const existingIndex = customThemes.findIndex(t => t.id === parsed.id);
      if (existingIndex >= 0) {
        // Replace existing custom theme with same ID
        setCustomThemes(prev => {
          const updated = [...prev];
          updated[existingIndex] = parsed;
          return updated;
        });
      } else {
        // Add new custom theme
        setCustomThemes(prev => [...prev, parsed]);
      }

      return parsed;
    } catch (e) {
      console.error('Failed to parse theme JSON:', e);
      return null;
    }
  }, [customThemes]);

  // Export a theme as JSON string
  const exportTheme = useCallback((themeId: string): string | null => {
    const found = availableThemes.find(t => t.id === themeId);
    if (!found) return null;
    return JSON.stringify(found, null, 2);
  }, [availableThemes]);

  // Remove a custom theme
  const removeCustomTheme = useCallback((themeId: string): boolean => {
    // Can't remove built-in themes
    if (builtInThemes.some(t => t.id === themeId)) {
      return false;
    }

    const existingIndex = customThemes.findIndex(t => t.id === themeId);
    if (existingIndex < 0) {
      return false;
    }

    setCustomThemes(prev => prev.filter(t => t.id !== themeId));

    // If current theme was removed, switch to default
    if (preferences.themeId === themeId) {
      setPreferences(prev => ({
        ...prev,
        themeId: DEFAULT_THEME_ID,
        favoriteThemeIds: (prev.favoriteThemeIds || []).filter(id => id !== themeId),
      }));
    } else {
      setPreferences(prev => ({
        ...prev,
        favoriteThemeIds: (prev.favoriteThemeIds || []).filter(id => id !== themeId),
      }));
    }

    return true;
  }, [customThemes, preferences.themeId]);

  const contextValue: ThemeContextValue = useMemo(() => ({
    theme,
    backgroundId: selectedBackground.id,
    backgroundOpacity: preferences.backgroundOpacity,
    taskLogOpacity: preferences.taskLogOpacity,
    mode: preferences.mode === 'system' ? effectiveMode : preferences.mode,
    effectiveMode,
    preferences,
    availableThemes,
    availableBackgrounds: backgroundPatterns,
    setTheme,
    setBackground,
    setBackgroundOpacity,
    setTaskLogOpacity,
    toggleFavoriteBackground,
    toggleFavoriteTheme,
    setMode,
    toggleMode,
    importTheme,
    exportTheme,
    removeCustomTheme,
  }), [
    theme,
    selectedBackground.id,
    preferences.backgroundOpacity,
    preferences.taskLogOpacity,
    effectiveMode,
    preferences,
    availableThemes,
    setBackground,
    setBackgroundOpacity,
    setTaskLogOpacity,
    toggleFavoriteBackground,
    toggleFavoriteTheme,
    setTheme,
    setMode,
    toggleMode,
    importTheme,
    exportTheme,
    removeCustomTheme,
  ]);

  return (
    <ThemeContext.Provider value={contextValue}>
      {isAnimatedBackgroundId(selectedBackground.id) && (
        <AnimatedBackground
          pattern={selectedBackground.id}
          darkMode={effectiveMode === 'dark'}
          opacity={preferences.backgroundOpacity}
        />
      )}
      {children}
    </ThemeContext.Provider>
  );
}

/**
 * Hook to access theme context
 */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
