import type { Theme } from './types';

const theme: Theme = {
  id: 'test',
  name: 'Test',
  colors: {
    accent: {
      50: '#fff',
      100: '#fff',
      200: '#fff',
      300: '#fff',
      400: '#fff',
      500: '#fff',
      600: '#fff',
      700: '#fff',
      800: '#fff',
      900: '#fff',
      950: '#fff',
    },
    neutral: {
      50: '#000',
      100: '#000',
      200: '#000',
      300: '#000',
      400: '#000',
      500: '#000',
      600: '#000',
      700: '#000',
      800: '#000',
      900: '#000',
      950: '#000',
    },
    success: '#0f0',
    warning: '#ff0',
    error: '#f00',
    info: '#00f',
  },
  light: {
    bgBase: '#fff',
    bgSurface: '#fff',
    border: '#ddd',
    text: '#111',
    textMuted: '#666',
    accentPrimary: '#0f0',
    accentHover: '#0c0',
    canvasGrid: '#eee',
    nodeGlow: '#0f0',
    scrollbarThumb: '#ccc',
  },
  dark: {
    bgBase: '#000',
    bgSurface: '#111',
    border: '#222',
    text: '#fff',
    textMuted: '#999',
    accentPrimary: '#0f0',
    accentHover: '#0c0',
    canvasGrid: '#222',
    nodeGlow: '#0f0',
    scrollbarThumb: '#333',
  },
};

describe('theme types', () => {
  it('accepts a Theme shape', () => {
    expect(theme.id).toBe('test');
  });
});
