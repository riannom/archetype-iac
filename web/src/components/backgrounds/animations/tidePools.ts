/**
 * Tide Pools Animation
 * Gentle ripples and caustic highlights in tidal pools
 */

import { useMemo, useRef } from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

interface TideRipple {
  x: number;
  y: number;
  radius: number;
  maxRadius: number;
  speed: number;
  opacity: number;
  colorScheme: number;
}

export function useTidePools(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const ripplesRef = useRef<TideRipple[]>([]);
  const timeRef = useRef<number>(0);

  const colorSchemes = useMemo(() => darkMode
    ? [
        [130, 200, 200],
        [140, 190, 195],
        [120, 185, 190],
      ]
    : [
        [80, 160, 165],
        [90, 150, 160],
        [70, 145, 155],
      ], [darkMode]);

  function createRipple(canvas: HTMLCanvasElement): TideRipple {
    return {
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      radius: 0,
      maxRadius: 40 + Math.random() * 60,
      speed: 0.3 + Math.random() * 0.3,
      opacity: 0.15 + Math.random() * 0.1,
      colorScheme: Math.floor(Math.random() * 3),
    };
  }

  useCanvasAnimation(
    canvasRef,
    active,
    {
      onInit: (_ctx, canvas) => {
        const rippleCount = Math.floor((canvas.width * canvas.height) / 80000) + 5;
        ripplesRef.current = Array.from({ length: rippleCount }, () => {
          const ripple = createRipple(canvas);
          ripple.radius = Math.random() * ripple.maxRadius;
          return ripple;
        });
      },

      onFrame: (ctx, canvas) => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        timeRef.current += 0.016;

        const opacityMultiplier = opacity / 50;

        ripplesRef.current.forEach((ripple, i) => {
          ripple.radius += ripple.speed;

          if (ripple.radius >= ripple.maxRadius) {
            ripplesRef.current[i] = createRipple(canvas);
          }

          const colors = colorSchemes[ripple.colorScheme];
          const fadeOut = 1 - ripple.radius / ripple.maxRadius;

          for (let j = 0; j < 3; j++) {
            const r = ripple.radius - j * 8;
            if (r > 0) {
              ctx.beginPath();
              ctx.arc(ripple.x, ripple.y, r, 0, Math.PI * 2);
              ctx.strokeStyle = `rgba(${colors[0]}, ${colors[1]}, ${colors[2]}, ${ripple.opacity * opacityMultiplier * fadeOut * (1 - j * 0.25)})`;
              ctx.lineWidth = 2.5 - j * 0.5;
              ctx.stroke();
            }
          }

          if (ripple.radius > 10) {
            const highlightAngle = timeRef.current * 0.5 + ripple.x * 0.01;
            const hx = ripple.x + Math.cos(highlightAngle) * ripple.radius * 0.3;
            const hy = ripple.y + Math.sin(highlightAngle) * ripple.radius * 0.3;

            ctx.beginPath();
            ctx.arc(hx, hy, 4, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(255, 255, 255, ${ripple.opacity * opacityMultiplier * fadeOut * 0.5})`;
            ctx.fill();
          }
        });
      },
    },
    [darkMode, opacity]
  );
}
