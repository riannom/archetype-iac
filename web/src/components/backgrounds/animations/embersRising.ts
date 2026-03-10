/**
 * Embers Rising Animation
 * Warm embers floating upward like campfire sparks
 */

import { useRef, useCallback } from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

interface Ember {
  x: number;
  y: number;
  size: number;
  speed: number;
  wobblePhase: number;
  wobbleSpeed: number;
  opacity: number;
  fadeSpeed: number;
  glowSize: number;
}

export function useEmbersRising(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  _darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const embersRef = useRef<Ember[]>([]);
  const timeRef = useRef<number>(0);

  const createEmber = useCallback((canvas: HTMLCanvasElement, startFromBottom = true): Ember => ({
    x: Math.random() * canvas.width,
    y: startFromBottom ? canvas.height + 10 : Math.random() * canvas.height,
    size: 1.5 + Math.random() * 3,
    speed: 0.4 + Math.random() * 0.8,
    wobblePhase: Math.random() * Math.PI * 2,
    wobbleSpeed: 1 + Math.random() * 2,
    opacity: 0.5 + Math.random() * 0.4,
    fadeSpeed: 0.002 + Math.random() * 0.003,
    glowSize: 8 + Math.random() * 12,
  }), []);

  useCanvasAnimation(
    canvasRef,
    active,
    {
      onInit: (_ctx, canvas) => {
        const emberCount = Math.floor((canvas.width * canvas.height) / 25000);
        embersRef.current = Array.from({ length: Math.max(20, emberCount) }, () =>
          createEmber(canvas, false)
        );
      },

      onFrame: (ctx, canvas) => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        timeRef.current += 0.016;

        const opacityMultiplier = opacity / 50;

        embersRef.current.forEach((ember, index) => {
          ember.y -= ember.speed;
          ember.x += Math.sin(timeRef.current * ember.wobbleSpeed + ember.wobblePhase) * 0.4;
          ember.opacity -= ember.fadeSpeed;

          if (ember.opacity <= 0 || ember.y < -20) {
            embersRef.current[index] = createEmber(canvas, true);
            return;
          }

          const alpha = ember.opacity * opacityMultiplier;

          const colorShift = Math.sin(timeRef.current * 3 + ember.wobblePhase) * 0.5 + 0.5;
          const emberColor = {
            r: 255,
            g: Math.floor(100 + colorShift * 80),
            b: Math.floor(20 + colorShift * 30),
          };

          const gradient = ctx.createRadialGradient(
            ember.x, ember.y, 0,
            ember.x, ember.y, ember.glowSize
          );
          gradient.addColorStop(0, `rgba(${emberColor.r}, ${emberColor.g}, ${emberColor.b}, ${alpha * 0.4})`);
          gradient.addColorStop(0.3, `rgba(${emberColor.r}, ${emberColor.g - 30}, ${emberColor.b}, ${alpha * 0.2})`);
          gradient.addColorStop(1, `rgba(${emberColor.r}, ${emberColor.g - 50}, ${emberColor.b}, 0)`);

          ctx.fillStyle = gradient;
          ctx.beginPath();
          ctx.arc(ember.x, ember.y, ember.glowSize, 0, Math.PI * 2);
          ctx.fill();

          ctx.fillStyle = `rgba(255, 220, 150, ${alpha})`;
          ctx.beginPath();
          ctx.arc(ember.x, ember.y, ember.size, 0, Math.PI * 2);
          ctx.fill();
        });
      },
    },
    [opacity, createEmber]
  );
}
