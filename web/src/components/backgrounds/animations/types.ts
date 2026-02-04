/**
 * Shared types for animated backgrounds
 */

import React from 'react';

/**
 * Common hook signature for all animation hooks
 */
export type AnimationHook = (
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) => void;

/**
 * Common refs used across animations
 */
export interface AnimationRefs<T> {
  items: React.MutableRefObject<T[]>;
  animation: React.MutableRefObject<number | undefined>;
  time: React.MutableRefObject<number>;
}
