import React from 'react';
import { render } from '@testing-library/react';
import { vi } from 'vitest';

vi.mock('./animations', () => ({
  useSakuraPetals: vi.fn(),
  useConstellation: vi.fn(),
  useSnowfall: vi.fn(),
  useFireflies: vi.fn(),
  useInkDrops: vi.fn(),
  useRipplingWater: vi.fn(),
  useFallingLeaves: vi.fn(),
  useEmbersRising: vi.fn(),
  useGentleRain: vi.fn(),
  useKoiShadows: vi.fn(),
  useLotusBloom: vi.fn(),
  useFloatingLanterns: vi.fn(),
  useMoonlitClouds: vi.fn(),
  useTidePools: vi.fn(),
  useTrainStation: vi.fn(),
  useSereneMeadows: vi.fn(),
  useStillPonds: vi.fn(),
  useDesertDunes: vi.fn(),
  useDucklingParade: vi.fn(),
  useBunnyMeadow: vi.fn(),
  useStargazing: vi.fn(),
  useMountainMist: vi.fn(),
  useLavenderFields: vi.fn(),
  useSunsetSailing: vi.fn(),
  useRaindropWindow: vi.fn(),
  useButterflyGarden: vi.fn(),
  useDandelionWishes: vi.fn(),
  useMistyValley: vi.fn(),
  useGentleWaves: vi.fn(),
  useJellyfishDrift: vi.fn(),
  useSakuraRedux: vi.fn(),
  useFireworks: vi.fn(),
  useIceCrystals: vi.fn(),
  useAutumnWind: vi.fn(),
  useBreath: vi.fn(),
  useMyceliumNetwork: vi.fn(),
  useOilSlick: vi.fn(),
  useBioluminescentBeach: vi.fn(),
  useTidalPatterns: vi.fn(),
  usePaperBoats: vi.fn(),
  usePaperAirplanes: vi.fn(),
  useThunderstorm: vi.fn(),
}));

import { AnimatedBackground, isAnimatedPattern } from './AnimatedBackground';

describe('AnimatedBackground', () => {
  it('returns null for non-animated patterns', () => {
    const { container } = render(
      <AnimatedBackground pattern="minimal" darkMode={false} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders a canvas for animated patterns', () => {
    const { container } = render(
      <AnimatedBackground pattern="breath" darkMode={false} opacity={80} />
    );
    const canvas = container.querySelector('canvas');
    expect(canvas).toBeInTheDocument();
  });

  it('detects animated pattern ids', () => {
    expect(isAnimatedPattern('breath')).toBe(true);
    expect(isAnimatedPattern('minimal')).toBe(false);
  });
});
