export const THEME_BACKGROUND_SUGGESTIONS: Record<string, string> = {
  'sage-stone': 'breath',
  'ocean': 'gentle-waves',
  'copper': 'embers-rising',
  'violet': 'constellation',
  'rose': 'sakura-redux',
  'serenity': 'gentle-waves',
  'forest': 'mountain-mist',
  'cyber': 'constellation',
  'sunrise': 'sunset-sailing',
  'sunset': 'sakura-redux',
  'sakura-yoshino': 'sakura-redux',
  'sakura-sumie': 'mountain-mist',
  'midnight': 'stargazing',
  'desert': 'desert-dunes',
  'seasonal': 'gentle-rain',
};

export function getSuggestedBackgroundForTheme(themeId: string): string {
  return THEME_BACKGROUND_SUGGESTIONS[themeId] || 'minimal';
}
