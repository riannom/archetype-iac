/**
 * Fireflies Animation
 * Softly glowing fireflies drifting in the night
 */

import { useRef, useEffect, useCallback } from 'react';

interface Firefly {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  glowPhase: number;
  glowSpeed: number;
  maxBrightness: number;
  driftPhase: number;
}

export function useFireflies(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const firefliesRef = useRef<Firefly[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  const createFirefly = useCallback((canvas: HTMLCanvasElement): Firefly => {
    return {
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      size: 2 + Math.random() * 3,
      glowPhase: Math.random() * Math.PI * 2,
      glowSpeed: 0.5 + Math.random() * 1.5,
      maxBrightness: 0.4 + Math.random() * 0.5,
      driftPhase: Math.random() * Math.PI * 2,
    };
  }, []);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const fireflyCount = Math.floor((canvas.width * canvas.height) / 40000);
    firefliesRef.current = Array.from({ length: Math.max(15, fireflyCount) }, () =>
      createFirefly(canvas)
    );

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      firefliesRef.current.forEach((firefly) => {
        firefly.x += firefly.vx + Math.sin(timeRef.current * 0.5 + firefly.driftPhase) * 0.2;
        firefly.y += firefly.vy + Math.cos(timeRef.current * 0.3 + firefly.driftPhase) * 0.15;

        if (firefly.x < -20) firefly.x = canvas.width + 20;
        if (firefly.x > canvas.width + 20) firefly.x = -20;
        if (firefly.y < -20) firefly.y = canvas.height + 20;
        if (firefly.y > canvas.height + 20) firefly.y = -20;

        const glowCycle = Math.sin(timeRef.current * firefly.glowSpeed + firefly.glowPhase);
        const brightness = Math.max(0, glowCycle) * firefly.maxBrightness;

        if (brightness > 0.05) {
          const warmColor = darkMode
            ? { r: 255, g: 230, b: 100 }
            : { r: 200, g: 180, b: 50 };

          const glowSize = firefly.size * 4 * (0.5 + brightness * 0.5);
          const gradient = ctx.createRadialGradient(
            firefly.x, firefly.y, 0,
            firefly.x, firefly.y, glowSize
          );
          gradient.addColorStop(0, `rgba(${warmColor.r}, ${warmColor.g}, ${warmColor.b}, ${brightness * 0.6 * opacityMultiplier})`);
          gradient.addColorStop(0.4, `rgba(${warmColor.r}, ${warmColor.g}, ${warmColor.b}, ${brightness * 0.2 * opacityMultiplier})`);
          gradient.addColorStop(1, `rgba(${warmColor.r}, ${warmColor.g}, ${warmColor.b}, 0)`);

          ctx.fillStyle = gradient;
          ctx.beginPath();
          ctx.arc(firefly.x, firefly.y, glowSize, 0, Math.PI * 2);
          ctx.fill();

          ctx.fillStyle = `rgba(255, 255, 200, ${brightness * opacityMultiplier})`;
          ctx.beginPath();
          ctx.arc(firefly.x, firefly.y, firefly.size * 0.5, 0, Math.PI * 2);
          ctx.fill();
        }
      });

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [canvasRef, darkMode, opacity, createFirefly, active]);
}
