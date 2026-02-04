/**
 * Animated Background Component
 *
 * Renders canvas-based animated backgrounds for special patterns.
 * Supports multiple animation types for different visual effects.
 */

import React, { useRef } from 'react';

// Import all animation hooks
import {
  useSakuraPetals,
  useConstellation,
  useSnowfall,
  useFireflies,
  useInkDrops,
  useRipplingWater,
  useFallingLeaves,
  useEmbersRising,
  useGentleRain,
  useKoiShadows,
  useLotusBloom,
  useFloatingLanterns,
  useMoonlitClouds,
  useTidePools,
  useTrainStation,
  useSereneMeadows,
  useStillPonds,
  useDesertDunes,
  useDucklingParade,
  useBunnyMeadow,
  useStargazing,
  useMountainMist,
  // New serene animations
  useLavenderFields,
  useSunsetSailing,
  useRaindropWindow,
  // Replacement animations
  useButterflyGarden,
  useDandelionWishes,
  useMistyValley,
  useGentleWaves,
  // Additional serene animations
  useJellyfishDrift,
  useSakuraRedux,
  useFireworks,
  // New animations
  useIceCrystals,
  useAutumnWind,
  // Abstract animations
  useBreath,
  useMyceliumNetwork,
  useOilSlick,
  // New landscape/nature animations
  useBioluminescentBeach,
  useTidalPatterns,
  usePaperBoats,
  usePaperAirplanes,
  useThunderstorm,
} from './animations';

interface AnimatedBackgroundProps {
  pattern: string;
  darkMode: boolean;
  opacity?: number; // 0-100, default 50
}

// List of all animated pattern IDs
export const ANIMATED_PATTERNS = [
  'sakura-petals',
  'constellation',
  'snowfall',
  'fireflies',
  'ink-drops',
  'rippling-water',
  'falling-leaves',
  'embers-rising',
  'gentle-rain',
  // Sumi-e (ink wash) animations
  'koi-shadows',
  // Zen & nature animations
  'lotus-bloom',
  'floating-lanterns',
  'moonlit-clouds',
  'tide-pools',
  // Fun animations
  'train-station',
  // Landscape animations
  'serene-meadows',
  'still-ponds',
  'desert-dunes',
  'mountain-mist',
  // Cute animals
  'duckling-parade',
  'bunny-meadow',
  // Night sky
  'stargazing',
  // New serene animations
  'lavender-fields',
  'sunset-sailing',
  'raindrop-window',
  // Replacement animations (replacing static patterns)
  'butterfly-garden',
  'dandelion-wishes',
  'misty-valley',
  'gentle-waves',
  // Additional serene animations
  'jellyfish-drift',
  'sakura-redux',
  'fireworks',
  // New animations
  'ice-crystals',
  'autumn-wind',
  // Abstract animations
  'breath',
  'mycelium-network',
  'oil-slick',
  // New landscape/nature animations
  'bioluminescent-beach',
  'tidal-patterns',
  'paper-boats',
  'paper-airplanes',
  'thunderstorm',
] as const;

export type AnimatedPatternId = typeof ANIMATED_PATTERNS[number];

export function isAnimatedPattern(pattern: string): pattern is AnimatedPatternId {
  return ANIMATED_PATTERNS.includes(pattern as AnimatedPatternId);
}

export const AnimatedBackground: React.FC<AnimatedBackgroundProps> = ({
  pattern,
  darkMode,
  opacity = 50,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const isAnimated = isAnimatedPattern(pattern);

  // Call all hooks but only activate the one that matches the pattern
  useSakuraPetals(canvasRef, darkMode, opacity, pattern === 'sakura-petals');
  useConstellation(canvasRef, darkMode, opacity, pattern === 'constellation');
  useSnowfall(canvasRef, darkMode, opacity, pattern === 'snowfall');
  useFireflies(canvasRef, darkMode, opacity, pattern === 'fireflies');
  useInkDrops(canvasRef, darkMode, opacity, pattern === 'ink-drops');
  useRipplingWater(canvasRef, darkMode, opacity, pattern === 'rippling-water');
  useFallingLeaves(canvasRef, darkMode, opacity, pattern === 'falling-leaves');
  useEmbersRising(canvasRef, darkMode, opacity, pattern === 'embers-rising');
  useGentleRain(canvasRef, darkMode, opacity, pattern === 'gentle-rain');
  // Sumi-e animations
  useKoiShadows(canvasRef, darkMode, opacity, pattern === 'koi-shadows');
  // Zen & nature animations
  useLotusBloom(canvasRef, darkMode, opacity, pattern === 'lotus-bloom');
  useFloatingLanterns(canvasRef, darkMode, opacity, pattern === 'floating-lanterns');
  useMoonlitClouds(canvasRef, darkMode, opacity, pattern === 'moonlit-clouds');
  useTidePools(canvasRef, darkMode, opacity, pattern === 'tide-pools');
  // Fun animations
  useTrainStation(canvasRef, darkMode, opacity, pattern === 'train-station');
  // Landscape animations
  useSereneMeadows(canvasRef, darkMode, opacity, pattern === 'serene-meadows');
  useStillPonds(canvasRef, darkMode, opacity, pattern === 'still-ponds');
  useDesertDunes(canvasRef, darkMode, opacity, pattern === 'desert-dunes');
  useMountainMist(canvasRef, darkMode, opacity, pattern === 'mountain-mist');
  // Cute animals
  useDucklingParade(canvasRef, darkMode, opacity, pattern === 'duckling-parade');
  useBunnyMeadow(canvasRef, darkMode, opacity, pattern === 'bunny-meadow');
  // Night sky
  useStargazing(canvasRef, darkMode, opacity, pattern === 'stargazing');
  // New serene animations
  useLavenderFields(canvasRef, darkMode, opacity, pattern === 'lavender-fields');
  useSunsetSailing(canvasRef, darkMode, opacity, pattern === 'sunset-sailing');
  useRaindropWindow(canvasRef, darkMode, opacity, pattern === 'raindrop-window');
  // Replacement animations
  useButterflyGarden(canvasRef, darkMode, opacity, pattern === 'butterfly-garden');
  useDandelionWishes(canvasRef, darkMode, opacity, pattern === 'dandelion-wishes');
  useMistyValley(canvasRef, darkMode, opacity, pattern === 'misty-valley');
  useGentleWaves(canvasRef, darkMode, opacity, pattern === 'gentle-waves');
  // Additional serene animations
  useJellyfishDrift(canvasRef, darkMode, opacity, pattern === 'jellyfish-drift');
  useSakuraRedux(canvasRef, darkMode, opacity, pattern === 'sakura-redux');
  useFireworks(canvasRef, darkMode, opacity, pattern === 'fireworks');
  // New animations
  useIceCrystals(canvasRef, darkMode, opacity, pattern === 'ice-crystals');
  useAutumnWind(canvasRef, darkMode, opacity, pattern === 'autumn-wind');
  // Abstract animations
  useBreath(canvasRef, darkMode, opacity, pattern === 'breath');
  useMyceliumNetwork(canvasRef, darkMode, opacity, pattern === 'mycelium-network');
  useOilSlick(canvasRef, darkMode, opacity, pattern === 'oil-slick');
  // New landscape/nature animations
  useBioluminescentBeach(canvasRef, darkMode, opacity, pattern === 'bioluminescent-beach');
  useTidalPatterns(canvasRef, darkMode, opacity, pattern === 'tidal-patterns');
  usePaperBoats(canvasRef, darkMode, opacity, pattern === 'paper-boats');
  usePaperAirplanes(canvasRef, darkMode, opacity, pattern === 'paper-airplanes');
  useThunderstorm(canvasRef, darkMode, opacity, pattern === 'thunderstorm');

  if (!isAnimated) {
    return null;
  }

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none"
      style={{
        zIndex: 0,
        opacity: opacity / 100,
      }}
      aria-hidden="true"
    />
  );
};

export default AnimatedBackground;
