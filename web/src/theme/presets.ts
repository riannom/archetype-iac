import type { Theme, ColorScale } from './types';

// Lime/Sage color scale (current default)
const sageAccent: ColorScale = {
  50: '#F7FEE7',
  100: '#ECFCCB',
  200: '#D9F99D',
  300: '#BEF264',
  400: '#A3E635',
  500: '#84CC16',
  600: '#65A30D',
  700: '#4D7C0F',
  800: '#3F6212',
  900: '#365314',
  950: '#1A2E05',
};

// Stone neutral scale
const stoneNeutral: ColorScale = {
  50: '#FAFAF9',
  100: '#F5F5F4',
  200: '#E7E5E4',
  300: '#D6D3D1',
  400: '#A8A29E',
  500: '#78716C',
  600: '#57534E',
  700: '#44403C',
  800: '#292524',
  900: '#1C1917',
  950: '#0C0A09',
};

// Ocean/Blue color scale
const oceanAccent: ColorScale = {
  50: '#EFF6FF',
  100: '#DBEAFE',
  200: '#BFDBFE',
  300: '#93C5FD',
  400: '#60A5FA',
  500: '#3B82F6',
  600: '#2563EB',
  700: '#1D4ED8',
  800: '#1E40AF',
  900: '#1E3A8A',
  950: '#172554',
};

// Slate neutral scale (pairs with ocean)
const slateNeutral: ColorScale = {
  50: '#F8FAFC',
  100: '#F1F5F9',
  200: '#E2E8F0',
  300: '#CBD5E1',
  400: '#94A3B8',
  500: '#64748B',
  600: '#475569',
  700: '#334155',
  800: '#1E293B',
  900: '#0F172A',
  950: '#020617',
};

// Copper/Orange color scale
const copperAccent: ColorScale = {
  50: '#FFF7ED',
  100: '#FFEDD5',
  200: '#FED7AA',
  300: '#FDBA74',
  400: '#FB923C',
  500: '#F97316',
  600: '#EA580C',
  700: '#C2410C',
  800: '#9A3412',
  900: '#7C2D12',
  950: '#431407',
};

// Warm neutral scale (pairs with copper)
const warmNeutral: ColorScale = {
  50: '#FAFAF9',
  100: '#F5F5F4',
  200: '#E7E5E4',
  300: '#D6D3D1',
  400: '#A8A29E',
  500: '#78716C',
  600: '#57534E',
  700: '#44403C',
  800: '#292524',
  900: '#1C1917',
  950: '#0C0A09',
};

// Violet/Purple color scale
const violetAccent: ColorScale = {
  50: '#F5F3FF',
  100: '#EDE9FE',
  200: '#DDD6FE',
  300: '#C4B5FD',
  400: '#A78BFA',
  500: '#8B5CF6',
  600: '#7C3AED',
  700: '#6D28D9',
  800: '#5B21B6',
  900: '#4C1D95',
  950: '#2E1065',
};

// Zinc neutral scale (pairs with violet)
const zincNeutral: ColorScale = {
  50: '#FAFAFA',
  100: '#F4F4F5',
  200: '#E4E4E7',
  300: '#D4D4D8',
  400: '#A1A1AA',
  500: '#71717A',
  600: '#52525B',
  700: '#3F3F46',
  800: '#27272A',
  900: '#18181B',
  950: '#09090B',
};

// Rose/Pink color scale
const roseAccent: ColorScale = {
  50: '#FFF1F2',
  100: '#FFE4E6',
  200: '#FECDD3',
  300: '#FDA4AF',
  400: '#FB7185',
  500: '#F43F5E',
  600: '#E11D48',
  700: '#BE123C',
  800: '#9F1239',
  900: '#881337',
  950: '#4C0519',
};

// Neutral scale (pairs with rose)
const neutralGray: ColorScale = {
  50: '#FAFAFA',
  100: '#F5F5F5',
  200: '#E5E5E5',
  300: '#D4D4D4',
  400: '#A3A3A3',
  500: '#737373',
  600: '#525252',
  700: '#404040',
  800: '#262626',
  900: '#171717',
  950: '#0A0A0A',
};

// Semantic colors (shared across all themes)
const semanticColors = {
  success: '#22C55E',
  warning: '#F59E0B',
  error: '#EF4444',
  info: '#06B6D4',
};

/**
 * Sage & Stone - Earthy, calm, natural (default theme)
 */
export const sageStoneTheme: Theme = {
  id: 'sage-stone',
  name: 'Sage & Stone',
  description: 'Earthy lime green accents with warm stone neutrals',
  colors: {
    accent: sageAccent,
    neutral: stoneNeutral,
    ...semanticColors,
  },
  light: {
    bgBase: '#FAFAF9',
    bgSurface: '#F5F5F4',
    border: '#E7E5E4',
    text: '#1C1917',
    textMuted: '#78716C',
    accentPrimary: '#65A30D',
    accentHover: '#4D7C0F',
    canvasGrid: 'rgba(120, 113, 108, 0.15)',
    nodeGlow: 'rgba(101, 163, 13, 0.3)',
    scrollbarThumb: '#A8A29E',
  },
  dark: {
    bgBase: '#1C1917',
    bgSurface: '#292524',
    border: '#44403C',
    text: '#FAFAF9',
    textMuted: '#A8A29E',
    accentPrimary: '#84CC16',
    accentHover: '#A3E635',
    canvasGrid: 'rgba(168, 162, 158, 0.12)',
    nodeGlow: 'rgba(132, 204, 22, 0.25)',
    scrollbarThumb: '#57534E',
  },
};

/**
 * Ocean - Cool, professional blue theme
 */
export const oceanTheme: Theme = {
  id: 'ocean',
  name: 'Ocean',
  description: 'Cool blue accents with slate neutrals',
  colors: {
    accent: oceanAccent,
    neutral: slateNeutral,
    ...semanticColors,
  },
  light: {
    bgBase: '#F8FAFC',
    bgSurface: '#F1F5F9',
    border: '#E2E8F0',
    text: '#0F172A',
    textMuted: '#64748B',
    accentPrimary: '#2563EB',
    accentHover: '#1D4ED8',
    canvasGrid: 'rgba(100, 116, 139, 0.15)',
    nodeGlow: 'rgba(37, 99, 235, 0.3)',
    scrollbarThumb: '#94A3B8',
  },
  dark: {
    bgBase: '#0F172A',
    bgSurface: '#1E293B',
    border: '#334155',
    text: '#F8FAFC',
    textMuted: '#94A3B8',
    accentPrimary: '#3B82F6',
    accentHover: '#60A5FA',
    canvasGrid: 'rgba(148, 163, 184, 0.12)',
    nodeGlow: 'rgba(59, 130, 246, 0.25)',
    scrollbarThumb: '#475569',
  },
};

/**
 * Copper - Warm, energetic orange theme
 */
export const copperTheme: Theme = {
  id: 'copper',
  name: 'Copper',
  description: 'Warm orange accents with earthy neutrals',
  colors: {
    accent: copperAccent,
    neutral: warmNeutral,
    ...semanticColors,
  },
  light: {
    bgBase: '#FAFAF9',
    bgSurface: '#F5F5F4',
    border: '#E7E5E4',
    text: '#1C1917',
    textMuted: '#78716C',
    accentPrimary: '#EA580C',
    accentHover: '#C2410C',
    canvasGrid: 'rgba(120, 113, 108, 0.15)',
    nodeGlow: 'rgba(234, 88, 12, 0.3)',
    scrollbarThumb: '#A8A29E',
  },
  dark: {
    bgBase: '#1C1917',
    bgSurface: '#292524',
    border: '#44403C',
    text: '#FAFAF9',
    textMuted: '#A8A29E',
    accentPrimary: '#F97316',
    accentHover: '#FB923C',
    canvasGrid: 'rgba(168, 162, 158, 0.12)',
    nodeGlow: 'rgba(249, 115, 22, 0.25)',
    scrollbarThumb: '#57534E',
  },
};

/**
 * Violet - Rich, creative purple theme
 */
export const violetTheme: Theme = {
  id: 'violet',
  name: 'Violet',
  description: 'Rich purple accents with zinc neutrals',
  colors: {
    accent: violetAccent,
    neutral: zincNeutral,
    ...semanticColors,
  },
  light: {
    bgBase: '#FAFAFA',
    bgSurface: '#F4F4F5',
    border: '#E4E4E7',
    text: '#18181B',
    textMuted: '#71717A',
    accentPrimary: '#7C3AED',
    accentHover: '#6D28D9',
    canvasGrid: 'rgba(113, 113, 122, 0.15)',
    nodeGlow: 'rgba(124, 58, 237, 0.3)',
    scrollbarThumb: '#A1A1AA',
  },
  dark: {
    bgBase: '#18181B',
    bgSurface: '#27272A',
    border: '#3F3F46',
    text: '#FAFAFA',
    textMuted: '#A1A1AA',
    accentPrimary: '#8B5CF6',
    accentHover: '#A78BFA',
    canvasGrid: 'rgba(161, 161, 170, 0.12)',
    nodeGlow: 'rgba(139, 92, 246, 0.25)',
    scrollbarThumb: '#52525B',
  },
};

/**
 * Rose - Soft, modern pink theme
 */
export const roseTheme: Theme = {
  id: 'rose',
  name: 'Rose',
  description: 'Soft pink accents with neutral grays',
  colors: {
    accent: roseAccent,
    neutral: neutralGray,
    ...semanticColors,
  },
  light: {
    bgBase: '#FAFAFA',
    bgSurface: '#F5F5F5',
    border: '#E5E5E5',
    text: '#171717',
    textMuted: '#737373',
    accentPrimary: '#E11D48',
    accentHover: '#BE123C',
    canvasGrid: 'rgba(115, 115, 115, 0.15)',
    nodeGlow: 'rgba(225, 29, 72, 0.3)',
    scrollbarThumb: '#A3A3A3',
  },
  dark: {
    bgBase: '#171717',
    bgSurface: '#262626',
    border: '#404040',
    text: '#FAFAFA',
    textMuted: '#A3A3A3',
    accentPrimary: '#F43F5E',
    accentHover: '#FB7185',
    canvasGrid: 'rgba(163, 163, 163, 0.12)',
    nodeGlow: 'rgba(244, 63, 94, 0.25)',
    scrollbarThumb: '#525252',
  },
};

/**
 * Serenity - Tropical cyan theme
 */
export const serenityTheme: Theme = {
  id: 'serenity',
  name: 'Serenity',
  description: 'Tropical cyan accents and tranquil slate tones',
  colors: {
    accent: {
      50: '#ECFEFF', 100: '#CFFAFE', 200: '#A5F3FC', 300: '#67E8F9', 400: '#22D3EE',
      500: '#06B6D4', 600: '#0891B2', 700: '#0E7490', 800: '#155E75', 900: '#164E63', 950: '#083344',
    },
    neutral: slateNeutral,
    ...semanticColors,
  },
  light: { ...oceanTheme.light, accentPrimary: '#0891B2', accentHover: '#0E7490', nodeGlow: 'rgba(8, 145, 178, 0.3)' },
  dark: { ...oceanTheme.dark, accentPrimary: '#06B6D4', accentHover: '#22D3EE', nodeGlow: 'rgba(6, 182, 212, 0.25)' },
};

export const forestTheme: Theme = {
  id: 'forest',
  name: 'Forest',
  description: 'Deep evergreen accents and earthy neutrals',
  colors: {
    accent: {
      50: '#ECFDF5', 100: '#D1FAE5', 200: '#A7F3D0', 300: '#6EE7B7', 400: '#34D399',
      500: '#10B981', 600: '#059669', 700: '#047857', 800: '#065F46', 900: '#064E3B', 950: '#022C22',
    },
    neutral: stoneNeutral,
    ...semanticColors,
  },
  light: { ...sageStoneTheme.light, accentPrimary: '#059669', accentHover: '#047857', nodeGlow: 'rgba(5, 150, 105, 0.3)' },
  dark: { ...sageStoneTheme.dark, accentPrimary: '#10B981', accentHover: '#34D399', nodeGlow: 'rgba(16, 185, 129, 0.25)' },
};

export const cyberTheme: Theme = {
  id: 'cyber',
  name: 'Cyber',
  description: 'Neon electric palette with dark graphite base',
  colors: {
    accent: {
      50: '#ECFEFF', 100: '#CFFAFE', 200: '#A5F3FC', 300: '#67E8F9', 400: '#22D3EE',
      500: '#06B6D4', 600: '#0891B2', 700: '#0E7490', 800: '#155E75', 900: '#164E63', 950: '#083344',
    },
    neutral: zincNeutral,
    ...semanticColors,
  },
  light: { ...violetTheme.light, accentPrimary: '#0891B2', accentHover: '#0E7490', nodeGlow: 'rgba(8, 145, 178, 0.3)' },
  dark: { ...violetTheme.dark, accentPrimary: '#22D3EE', accentHover: '#67E8F9', nodeGlow: 'rgba(34, 211, 238, 0.25)' },
};

export const sunriseTheme: Theme = {
  id: 'sunrise',
  name: 'Sunrise',
  description: 'Warm sunrise oranges and soft dawn neutrals',
  colors: {
    accent: {
      50: '#FFFBEB', 100: '#FEF3C7', 200: '#FDE68A', 300: '#FCD34D', 400: '#FBBF24',
      500: '#F59E0B', 600: '#D97706', 700: '#B45309', 800: '#92400E', 900: '#78350F', 950: '#451A03',
    },
    neutral: warmNeutral,
    ...semanticColors,
  },
  light: { ...copperTheme.light, accentPrimary: '#D97706', accentHover: '#B45309', nodeGlow: 'rgba(217, 119, 6, 0.3)' },
  dark: { ...copperTheme.dark, accentPrimary: '#F59E0B', accentHover: '#FBBF24', nodeGlow: 'rgba(245, 158, 11, 0.25)' },
};

export const sunsetTheme: Theme = {
  id: 'sunset',
  name: 'Sunset',
  description: 'Sunset pink and orange accents with dusky neutrals',
  colors: {
    accent: {
      50: '#FFF1F2', 100: '#FFE4E6', 200: '#FECDD3', 300: '#FDA4AF', 400: '#FB7185',
      500: '#F43F5E', 600: '#E11D48', 700: '#BE123C', 800: '#9F1239', 900: '#881337', 950: '#4C0519',
    },
    neutral: neutralGray,
    ...semanticColors,
  },
  light: { ...roseTheme.light },
  dark: { ...roseTheme.dark },
};

export const sakuraYoshinoTheme: Theme = {
  id: 'sakura-yoshino',
  name: 'Sakura Yoshino',
  description: 'Delicate cherry blossom tones',
  colors: {
    accent: roseAccent,
    neutral: slateNeutral,
    ...semanticColors,
  },
  light: { ...roseTheme.light, bgBase: '#FFF7FA', bgSurface: '#FFEFF5' },
  dark: { ...roseTheme.dark, bgBase: '#2A1B24', bgSurface: '#3A2633' },
};

export const sakuraSumieTheme: Theme = {
  id: 'sakura-sumie',
  name: 'Sakura Sumi-e',
  description: 'Ink-wash charcoal with sakura highlights',
  colors: {
    accent: roseAccent,
    neutral: zincNeutral,
    ...semanticColors,
  },
  light: { ...roseTheme.light, bgBase: '#F7F4F5', bgSurface: '#EFEBED' },
  dark: { ...roseTheme.dark, bgBase: '#191617', bgSurface: '#252123' },
};

export const midnightTheme: Theme = {
  id: 'midnight',
  name: 'Midnight',
  description: 'Deep indigo night theme',
  colors: {
    accent: {
      50: '#EEF2FF', 100: '#E0E7FF', 200: '#C7D2FE', 300: '#A5B4FC', 400: '#818CF8',
      500: '#6366F1', 600: '#4F46E5', 700: '#4338CA', 800: '#3730A3', 900: '#312E81', 950: '#1E1B4B',
    },
    neutral: slateNeutral,
    ...semanticColors,
  },
  light: { ...oceanTheme.light, accentPrimary: '#4F46E5', accentHover: '#4338CA', nodeGlow: 'rgba(79, 70, 229, 0.3)' },
  dark: { ...oceanTheme.dark, bgBase: '#0B1024', bgSurface: '#121A38', border: '#24325E', accentPrimary: '#6366F1', accentHover: '#818CF8', nodeGlow: 'rgba(99, 102, 241, 0.25)' },
};

export const desertTheme: Theme = {
  id: 'desert',
  name: 'Desert',
  description: 'Sandy neutrals and sun-baked accents',
  colors: {
    accent: copperAccent,
    neutral: {
      50: '#FFFBEB', 100: '#FEF3C7', 200: '#FDE68A', 300: '#FCD34D', 400: '#FBBF24',
      500: '#D6A552', 600: '#B68A3B', 700: '#8D6E2E', 800: '#6E551F', 900: '#4E3A15', 950: '#2A1E0C',
    },
    ...semanticColors,
  },
  light: { ...copperTheme.light, bgBase: '#FFF8ED', bgSurface: '#F9EEDB' },
  dark: { ...copperTheme.dark, bgBase: '#2A1F15', bgSurface: '#3A2B1E' },
};

export const seasonalTheme: Theme = {
  id: 'seasonal',
  name: 'Seasonal',
  description: 'Balanced palette designed to pair with rotating backgrounds',
  colors: {
    accent: oceanAccent,
    neutral: stoneNeutral,
    ...semanticColors,
  },
  light: { ...oceanTheme.light },
  dark: { ...sageStoneTheme.dark },
};

/**
 * All built-in themes
 */
export const builtInThemes: Theme[] = [
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
];

/**
 * Default theme ID
 */
export const DEFAULT_THEME_ID = 'sage-stone';

/**
 * Get a built-in theme by ID
 */
export function getBuiltInTheme(id: string): Theme | undefined {
  return builtInThemes.find(t => t.id === id);
}
