import * as animationsIndex from './index';
import type { AnimationHook, AnimationRefs } from './types';

import { useAutumnWind } from './autumnWind';
import { useBambooSway } from './bambooSway';
import { useBioluminescentBeach } from './bioluminescentBeach';
import { useBitcoinParticles } from './bitcoinParticles';
import { useBreath } from './breath';
import { useBunnyMeadow } from './bunnyMeadow';
import { useButterflyGarden } from './butterflyGarden';
import { useConstellation } from './constellation';
import { useDandelionWishes } from './dandelionWishes';
import { useDesertDunes } from './desertDunes';
import { useDigitalRain } from './digitalRain';
import { useDucklingParade } from './ducklingParade';
import { useEclipse } from './eclipse';
import { useEmbersRising } from './embersRising';
import { useFallingLeaves } from './fallingLeaves';
import { useFireflies } from './fireflies';
import { useFireworks } from './fireworks';
import { useFloatingLanterns } from './floatingLanterns';
import { useFloatingShields } from './floatingShields';
import { useGentleRain } from './gentleRain';
import { useGentleWaves } from './gentleWaves';
import { useHashStorm } from './hashStorm';
import { useIceCrystals } from './iceCrystals';
import { useInkDrops } from './inkDrops';
import { useJellyfishDrift } from './jellyfishDrift';
import { useKoiShadows } from './koiShadows';
import { useLavenderFields } from './lavenderFields';
import { useLotusBloom } from './lotusBloom';
import { useMistyValley } from './mistyValley';
import { useMoonlitClouds } from './moonlitClouds';
import { useMountainMist } from './mountainMist';
import { useMyceliumNetwork } from './myceliumNetwork';
import { useNorthernLights } from './northernLights';
import { useOilSlick } from './oilSlick';
import { usePaperAirplanes } from './paperAirplanes';
import { usePaperBoats } from './paperBoats';
import { useRaindropWindow } from './raindropWindow';
import { useRipplingWater } from './ripplingWater';
import { useSakuraPetals } from './sakuraPetals';
import { useSakuraRedux } from './sakuraRedux';
import { useSatsSymbol } from './satsSymbol';
import { useSereneMeadows } from './sereneMeadows';
import { useSmokeCalligraphy } from './smokeCalligraphy';
import { useSnowfall } from './snowfall';
import { useStackingBlocks } from './stackingBlocks';
import { useStargazing } from './stargazing';
import { useStillPonds } from './stillPonds';
import { useSunsetSailing } from './sunsetSailing';
import { useThunderstorm } from './thunderstorm';
import { useTidalPatterns } from './tidalPatterns';
import { useTidePools } from './tidePools';
import { useTrainStation } from './trainStation';
import { useVolcanicIslands } from './volcanicIslands';
import { useWindChimes } from './windChimes';
import { useWisteria } from './wisteria';
import { useZenSandGarden } from './zenSandGarden';

const hooks: AnimationHook[] = [
  useAutumnWind,
  useBambooSway,
  useBioluminescentBeach,
  useBitcoinParticles,
  useBreath,
  useBunnyMeadow,
  useButterflyGarden,
  useConstellation,
  useDandelionWishes,
  useDesertDunes,
  useDigitalRain,
  useDucklingParade,
  useEclipse,
  useEmbersRising,
  useFallingLeaves,
  useFireflies,
  useFireworks,
  useFloatingLanterns,
  useFloatingShields,
  useGentleRain,
  useGentleWaves,
  useHashStorm,
  useIceCrystals,
  useInkDrops,
  useJellyfishDrift,
  useKoiShadows,
  useLavenderFields,
  useLotusBloom,
  useMistyValley,
  useMoonlitClouds,
  useMountainMist,
  useMyceliumNetwork,
  useNorthernLights,
  useOilSlick,
  usePaperAirplanes,
  usePaperBoats,
  useRaindropWindow,
  useRipplingWater,
  useSakuraPetals,
  useSakuraRedux,
  useSatsSymbol,
  useSereneMeadows,
  useSmokeCalligraphy,
  useSnowfall,
  useStackingBlocks,
  useStargazing,
  useStillPonds,
  useSunsetSailing,
  useThunderstorm,
  useTidalPatterns,
  useTidePools,
  useTrainStation,
  useVolcanicIslands,
  useWindChimes,
  useWisteria,
  useZenSandGarden,
];

describe('animation hooks', () => {
  it('exports animation hooks and types', () => {
    void animationsIndex;
    const refs: AnimationRefs<string> = {
      items: { current: [] },
      animation: { current: 0 },
      time: { current: 0 },
    };
    expect(refs.items.current.length).toBe(0);

    hooks.forEach((hook) => {
      expect(typeof hook).toBe('function');
    });
  });
});
