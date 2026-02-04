import { ANIMATED_PATTERNS } from '../components/backgrounds/AnimatedBackground';

export type BackgroundCategory =
  | 'all'
  | 'minimal'
  | 'geometric'
  | 'nature'
  | 'weather'
  | 'water'
  | 'sky'
  | 'creatures'
  | 'landscape'
  | 'whimsical'
  | 'zen';

export interface BackgroundPattern {
  id: string;
  name: string;
  description?: string;
  animated: boolean;
  categories: BackgroundCategory[];
  preferredMode?: 'light' | 'dark' | 'both';
}

const animatedPatternSet = new Set<string>(ANIMATED_PATTERNS);

export const backgroundPatterns: BackgroundPattern[] = [
  { id: 'none', name: 'None', animated: false, categories: ['minimal'], preferredMode: 'both' },
  { id: 'minimal', name: 'Minimal', animated: false, categories: ['minimal'], preferredMode: 'both' },
  { id: 'zen', name: 'Zen Dots', animated: false, categories: ['minimal', 'zen'], preferredMode: 'both' },
  { id: 'topography', name: 'Topography', animated: false, categories: ['geometric', 'landscape'], preferredMode: 'light' },
  { id: 'waves', name: 'Waves', animated: false, categories: ['geometric', 'water'], preferredMode: 'light' },
  { id: 'lines', name: 'Lines', animated: false, categories: ['minimal', 'geometric'], preferredMode: 'both' },
  { id: 'triangles', name: 'Triangles', animated: false, categories: ['geometric'], preferredMode: 'both' },
  { id: 'stars', name: 'Stars', animated: false, categories: ['sky', 'minimal'], preferredMode: 'dark' },
  { id: 'aurora', name: 'Aurora', animated: false, categories: ['sky', 'weather'], preferredMode: 'dark' },
  { id: 'mountains', name: 'Mountains', animated: false, categories: ['landscape'], preferredMode: 'light' },
  { id: 'noise', name: 'Noise', animated: false, categories: ['minimal'], preferredMode: 'both' },
  { id: 'butterfly-garden', name: 'Butterfly Garden', animated: true, categories: ['nature', 'creatures', 'whimsical'], preferredMode: 'light' },
  { id: 'dandelion-wishes', name: 'Dandelion Wishes', animated: true, categories: ['nature', 'whimsical'], preferredMode: 'light' },
  { id: 'misty-valley', name: 'Misty Valley', animated: true, categories: ['landscape', 'zen'], preferredMode: 'both' },
  { id: 'gentle-waves', name: 'Gentle Waves', animated: true, categories: ['water', 'zen'], preferredMode: 'both' },
  { id: 'sakura-petals', name: 'Sakura Petals', animated: true, categories: ['nature', 'zen', 'whimsical'], preferredMode: 'both' },
  { id: 'constellation', name: 'Constellation', animated: true, categories: ['sky', 'geometric'], preferredMode: 'dark' },
  { id: 'snowfall', name: 'Snowfall', animated: true, categories: ['weather', 'whimsical'], preferredMode: 'dark' },
  { id: 'fireflies', name: 'Fireflies', animated: true, categories: ['nature', 'creatures', 'whimsical'], preferredMode: 'dark' },
  { id: 'ink-drops', name: 'Ink Drops', animated: true, categories: ['water', 'zen'], preferredMode: 'both' },
  { id: 'rippling-water', name: 'Rippling Water', animated: true, categories: ['water', 'zen'], preferredMode: 'both' },
  { id: 'falling-leaves', name: 'Falling Leaves', animated: true, categories: ['nature', 'weather'], preferredMode: 'both' },
  { id: 'embers-rising', name: 'Embers Rising', animated: true, categories: ['nature', 'weather'], preferredMode: 'dark' },
  { id: 'gentle-rain', name: 'Gentle Rain', animated: true, categories: ['weather', 'zen'], preferredMode: 'both' },
  { id: 'koi-shadows', name: 'Koi Shadows', animated: true, categories: ['zen', 'creatures', 'water'], preferredMode: 'dark' },
  { id: 'lotus-bloom', name: 'Lotus Bloom', animated: true, categories: ['nature', 'water', 'zen'], preferredMode: 'light' },
  { id: 'floating-lanterns', name: 'Floating Lanterns', animated: true, categories: ['whimsical', 'sky'], preferredMode: 'dark' },
  { id: 'moonlit-clouds', name: 'Moonlit Clouds', animated: true, categories: ['sky', 'zen'], preferredMode: 'dark' },
  { id: 'tide-pools', name: 'Tide Pools', animated: true, categories: ['water', 'nature', 'creatures'], preferredMode: 'light' },
  { id: 'train-station', name: 'Train Station', animated: true, categories: ['landscape', 'zen'], preferredMode: 'both' },
  { id: 'serene-meadows', name: 'Serene Meadows', animated: true, categories: ['nature', 'landscape', 'zen'], preferredMode: 'light' },
  { id: 'still-ponds', name: 'Still Ponds', animated: true, categories: ['water', 'landscape', 'zen'], preferredMode: 'light' },
  { id: 'desert-dunes', name: 'Desert Dunes', animated: true, categories: ['landscape'], preferredMode: 'light' },
  { id: 'mountain-mist', name: 'Mountain Mist', animated: true, categories: ['zen', 'landscape'], preferredMode: 'dark' },
  { id: 'duckling-parade', name: 'Duckling Parade', animated: true, categories: ['creatures', 'whimsical'], preferredMode: 'light' },
  { id: 'bunny-meadow', name: 'Bunny Meadow', animated: true, categories: ['creatures', 'nature', 'whimsical'], preferredMode: 'light' },
  { id: 'stargazing', name: 'Stargazing', animated: true, categories: ['sky', 'zen'], preferredMode: 'dark' },
  { id: 'lavender-fields', name: 'Lavender Fields', animated: true, categories: ['nature', 'landscape'], preferredMode: 'light' },
  { id: 'sunset-sailing', name: 'Sunset Sailing', animated: true, categories: ['water', 'sky', 'landscape'], preferredMode: 'light' },
  { id: 'raindrop-window', name: 'Raindrop Window', animated: true, categories: ['weather', 'zen'], preferredMode: 'dark' },
  { id: 'jellyfish-drift', name: 'Jellyfish Drift', animated: true, categories: ['water', 'creatures', 'whimsical'], preferredMode: 'dark' },
  { id: 'sakura-redux', name: 'Sakura Redux', animated: true, categories: ['zen', 'nature', 'whimsical'], preferredMode: 'both' },
  { id: 'fireworks', name: 'Fireworks', animated: true, categories: ['sky', 'whimsical'], preferredMode: 'dark' },
  { id: 'ice-crystals', name: 'Ice Crystals', animated: true, categories: ['weather', 'whimsical'], preferredMode: 'dark' },
  { id: 'autumn-wind', name: 'Autumn Wind', animated: true, categories: ['nature', 'weather'], preferredMode: 'both' },
  { id: 'breath', name: 'Breath', animated: true, categories: ['zen', 'minimal'], preferredMode: 'both' },
  { id: 'mycelium-network', name: 'Mycelium Network', animated: true, categories: ['nature', 'geometric'], preferredMode: 'dark' },
  { id: 'oil-slick', name: 'Oil Slick', animated: true, categories: ['geometric', 'whimsical'], preferredMode: 'dark' },
  { id: 'bioluminescent-beach', name: 'Bioluminescent Beach', animated: true, categories: ['water', 'creatures', 'whimsical'], preferredMode: 'dark' },
  { id: 'tidal-patterns', name: 'Tidal Patterns', animated: true, categories: ['water', 'geometric'], preferredMode: 'light' },
  { id: 'paper-boats', name: 'Paper Boats', animated: true, categories: ['whimsical', 'water'], preferredMode: 'light' },
  { id: 'paper-airplanes', name: 'Paper Airplanes', animated: true, categories: ['whimsical', 'sky'], preferredMode: 'light' },
  { id: 'thunderstorm', name: 'Thunderstorm', animated: true, categories: ['weather', 'sky'], preferredMode: 'dark' },
];

export const backgroundCategories: { id: BackgroundCategory; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'minimal', label: 'Minimal' },
  { id: 'geometric', label: 'Geometric' },
  { id: 'nature', label: 'Nature' },
  { id: 'weather', label: 'Weather' },
  { id: 'water', label: 'Water' },
  { id: 'sky', label: 'Sky' },
  { id: 'creatures', label: 'Creatures' },
  { id: 'landscape', label: 'Landscape' },
  { id: 'whimsical', label: 'Whimsical' },
  { id: 'zen', label: 'Zen' },
];

export function getBackgroundById(id: string): BackgroundPattern | undefined {
  return backgroundPatterns.find((pattern) => pattern.id === id);
}

export function isAnimatedBackgroundId(id: string): boolean {
  return animatedPatternSet.has(id);
}

export function filterBackgroundsByCategory(category: BackgroundCategory): BackgroundPattern[] {
  if (category === 'all') {
    return backgroundPatterns;
  }
  return backgroundPatterns.filter((pattern) => pattern.categories.includes(category));
}

export function isBackgroundPreferredInMode(
  backgroundId: string,
  mode: 'light' | 'dark'
): boolean {
  const pattern = getBackgroundById(backgroundId);
  if (!pattern || !pattern.preferredMode || pattern.preferredMode === 'both') {
    return true;
  }
  return pattern.preferredMode === mode;
}
