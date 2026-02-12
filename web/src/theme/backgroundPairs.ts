import { getBackgroundById } from './backgrounds';

type ThemeSuggestion = string | { light: string; dark: string };

export const THEME_BACKGROUND_SUGGESTIONS: Record<string, ThemeSuggestion> = {
  'sage-stone': 'breath',
  'ocean': 'gentle-waves',
  'copper': { light: 'autumn-wind', dark: 'embers-rising' },
  'violet': { light: 'sakura-redux', dark: 'constellation' },
  'rose': 'sakura-redux',
  'serenity': 'gentle-waves',
  'forest': { light: 'serene-meadows', dark: 'mountain-mist' },
  'cyber': { light: 'tidal-patterns', dark: 'constellation' },
  'sunrise': { light: 'sunset-sailing', dark: 'moonlit-clouds' },
  'sunset': 'sakura-redux',
  'sakura-yoshino': 'sakura-redux',
  'sakura-sumie': { light: 'misty-valley', dark: 'mountain-mist' },
  'midnight': { light: 'misty-valley', dark: 'stargazing' },
  'desert': { light: 'desert-dunes', dark: 'mountain-mist' },
  'seasonal': 'gentle-rain',
};

function isModeCompatibleBackground(backgroundId: string, mode: 'light' | 'dark'): boolean {
  const background = getBackgroundById(backgroundId);
  if (!background || !background.preferredMode || background.preferredMode === 'both') {
    return true;
  }
  return background.preferredMode === mode;
}

export function getSuggestedBackgroundForTheme(themeId: string, mode?: 'light' | 'dark'): string {
  const suggestion = THEME_BACKGROUND_SUGGESTIONS[themeId];
  if (!suggestion) {
    return 'minimal';
  }

  if (typeof suggestion === 'string') {
    if (!mode || isModeCompatibleBackground(suggestion, mode)) {
      return suggestion;
    }
    return mode === 'dark' ? 'stargazing' : 'misty-valley';
  }

  const modeKey = mode || 'light';
  const modeSuggestion = suggestion[modeKey];
  if (isModeCompatibleBackground(modeSuggestion, modeKey)) {
    return modeSuggestion;
  }

  return modeKey === 'dark' ? 'stargazing' : 'misty-valley';
}
