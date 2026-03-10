/**
 * Shared Canvas Animation Framework Hook
 *
 * Extracts common boilerplate from animation hooks:
 * - Canvas ref and 2D context setup
 * - Resize event handling (sets canvas to window dimensions)
 * - requestAnimationFrame loop with cleanup
 * - Active guard (no-op when inactive)
 *
 * Each animation provides callbacks for initialization and per-frame rendering.
 */

import { useEffect, RefObject } from 'react';

/**
 * Callbacks that each animation provides to customize behavior.
 */
export interface CanvasAnimationCallbacks {
  /**
   * Called once on mount and again on every resize.
   * Use this to (re)initialize scene state based on canvas dimensions.
   * If not provided, no initialization logic runs beyond setting canvas size.
   */
  onInit?: (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement) => void;

  /**
   * Called once per animation frame. Draw your scene here.
   * Return `false` to stop the animation loop (optional, defaults to continuing).
   */
  onFrame: (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement) => void | false;

  /**
   * Called on cleanup (unmount or deps change) before the resize listener
   * and animation frame are torn down. Use for any custom cleanup.
   */
  onCleanup?: () => void;
}

/**
 * Shared hook that manages canvas lifecycle for all animations.
 *
 * @param canvasRef - React ref to the canvas element
 * @param active - Whether this animation is currently active
 * @param callbacks - Animation-specific init/frame/cleanup functions
 * @param deps - Additional dependency array items that should trigger re-mount
 *               (e.g., darkMode, opacity). The hook already depends on
 *               canvasRef and active internally.
 */
export function useCanvasAnimation(
  canvasRef: RefObject<HTMLCanvasElement>,
  active: boolean,
  callbacks: CanvasAnimationCallbacks,
  deps: React.DependencyList = []
) {
  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationId: number;
    let stopped = false;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      callbacks.onInit?.(ctx, canvas);
    };

    // Initial sizing + init
    resize();
    window.addEventListener('resize', resize);

    const animate = () => {
      if (stopped) return;

      const result = callbacks.onFrame(ctx, canvas);
      if (result === false) return;

      animationId = requestAnimationFrame(animate);
    };

    animationId = requestAnimationFrame(animate);

    return () => {
      stopped = true;
      window.removeEventListener('resize', resize);
      cancelAnimationFrame(animationId);
      callbacks.onCleanup?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasRef, active, ...deps]);
}
