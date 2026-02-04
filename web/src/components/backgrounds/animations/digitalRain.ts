/**
 * Digital Rain Animation
 * Matrix-style falling characters with Bitcoin theme
 */

import { useRef, useEffect, useCallback } from 'react';

interface RainDrop {
  x: number;
  y: number;
  speed: number;
  chars: string[];
  charIndex: number;
  opacity: number;
  length: number;
}

export function useDigitalRain(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const dropsRef = useRef<RainDrop[]>([]);
  const animationRef = useRef<number | undefined>(undefined);

  const chars = '01₿₿₿SATOSHI'.split('');

  const createDrop = useCallback((canvas: HTMLCanvasElement, startFromTop = true): RainDrop => {
    const length = 5 + Math.floor(Math.random() * 15);
    return {
      x: Math.floor(Math.random() * (canvas.width / 16)) * 16,
      y: startFromTop ? -length * 16 : Math.random() * canvas.height,
      speed: 0.5 + Math.random() * 1,
      chars: Array.from({ length }, () => chars[Math.floor(Math.random() * chars.length)]),
      charIndex: 0,
      opacity: 0.15 + Math.random() * 0.2,
      length,
    };
  }, [chars]);

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

    const dropCount = Math.floor(canvas.width / 50);
    dropsRef.current = Array.from({ length: dropCount }, () =>
      createDrop(canvas, false)
    );

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const opacityMultiplier = opacity / 50;

      ctx.font = '14px monospace';

      dropsRef.current.forEach((drop, index) => {
        for (let i = 0; i < drop.length; i++) {
          const charY = drop.y - i * 16;
          if (charY < 0 || charY > canvas.height) continue;

          const fadeRatio = 1 - i / drop.length;
          const alpha = drop.opacity * fadeRatio * opacityMultiplier;

          if (i === 0) {
            // Lead character is brighter
            ctx.fillStyle = darkMode
              ? `rgba(180, 220, 180, ${alpha * 1.5})`
              : `rgba(60, 140, 60, ${alpha * 1.5})`;
          } else {
            ctx.fillStyle = darkMode
              ? `rgba(140, 180, 140, ${alpha})`
              : `rgba(50, 120, 50, ${alpha})`;
          }

          ctx.fillText(drop.chars[i], drop.x, charY);
        }

        drop.y += drop.speed;

        if (Math.random() < 0.01) {
          const changeIndex = Math.floor(Math.random() * drop.length);
          drop.chars[changeIndex] = chars[Math.floor(Math.random() * chars.length)];
        }

        if (drop.y > canvas.height + drop.length * 16) {
          dropsRef.current[index] = createDrop(canvas, true);
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
  }, [canvasRef, darkMode, opacity, createDrop, chars, active]);
}
