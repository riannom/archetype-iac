import { describe, it, expect, vi, afterEach } from 'vitest';

import * as backgrounds from './backgrounds';
import { getSuggestedBackgroundForTheme } from './backgroundPairs';

const realGetBackgroundById = backgrounds.getBackgroundById;

afterEach(() => {
  vi.restoreAllMocks();
});

describe('backgroundPairs', () => {
  describe('basic mappings', () => {
    it('returns a suggested background or default', () => {
      expect(getSuggestedBackgroundForTheme('sage-stone')).toBe('breath');
      expect(getSuggestedBackgroundForTheme('unknown')).toBe('minimal');
    });

    it('returns mode-specific suggestions when configured', () => {
      expect(getSuggestedBackgroundForTheme('sunrise', 'light')).toBe('sunset-sailing');
      expect(getSuggestedBackgroundForTheme('sunrise', 'dark')).toBe('moonlit-clouds');
    });

    it('keeps suggestions mode-compatible', () => {
      expect(getSuggestedBackgroundForTheme('copper', 'dark')).toBe('embers-rising');
      expect(getSuggestedBackgroundForTheme('copper', 'light')).toBe('autumn-wind');
    });

    it('honors string suggestions when no mode is provided', () => {
      // Triggers the !mode branch in the string-suggestion path
      expect(getSuggestedBackgroundForTheme('rose')).toBe('sakura-redux');
    });

    it('defaults to light mode for object suggestions when no mode is provided', () => {
      expect(getSuggestedBackgroundForTheme('forest')).toBe('serene-meadows');
    });
  });

  describe('mode-incompatibility fallbacks', () => {
    // All current production suggestions happen to be mode-compatible, so
    // these paths require a stub to surface. We force getBackgroundById to
    // return mode-restricted entries for the specific suggested IDs.
    it('falls back to stargazing for dark mode when a string suggestion is light-only', () => {
      vi.spyOn(backgrounds, 'getBackgroundById').mockImplementation((id) => {
        if (id === 'breath') {
          return { id: 'breath', name: 'Breath', animated: true, categories: [], preferredMode: 'light' };
        }
        return realGetBackgroundById(id);
      });
      // 'sage-stone' suggests the string 'breath' — forced light-only above
      expect(getSuggestedBackgroundForTheme('sage-stone', 'dark')).toBe('stargazing');
    });

    it('falls back to misty-valley for light mode when a string suggestion is dark-only', () => {
      vi.spyOn(backgrounds, 'getBackgroundById').mockImplementation((id) => {
        if (id === 'gentle-waves') {
          return { id: 'gentle-waves', name: 'Gentle Waves', animated: true, categories: [], preferredMode: 'dark' };
        }
        return realGetBackgroundById(id);
      });
      // 'ocean' suggests the string 'gentle-waves' — forced dark-only above
      expect(getSuggestedBackgroundForTheme('ocean', 'light')).toBe('misty-valley');
    });

    it('falls back to stargazing for dark mode when an object suggestion is light-only', () => {
      vi.spyOn(backgrounds, 'getBackgroundById').mockImplementation((id) => {
        if (id === 'mountain-mist') {
          return { id: 'mountain-mist', name: 'Mountain Mist', animated: true, categories: [], preferredMode: 'light' };
        }
        return realGetBackgroundById(id);
      });
      // 'desert' { dark: 'mountain-mist' } — forced light-only above
      expect(getSuggestedBackgroundForTheme('desert', 'dark')).toBe('stargazing');
    });

    it('falls back to misty-valley for light mode when an object suggestion is dark-only', () => {
      vi.spyOn(backgrounds, 'getBackgroundById').mockImplementation((id) => {
        if (id === 'serene-meadows') {
          return { id: 'serene-meadows', name: 'Serene Meadows', animated: true, categories: [], preferredMode: 'dark' };
        }
        return realGetBackgroundById(id);
      });
      // 'forest' { light: 'serene-meadows' } — forced dark-only above
      expect(getSuggestedBackgroundForTheme('forest', 'light')).toBe('misty-valley');
    });
  });
});
