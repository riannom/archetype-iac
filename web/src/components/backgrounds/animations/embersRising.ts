/**
 * Embers Rising Animation
 * Warm embers floating upward like campfire sparks
 */

import { useRef, useEffect, useCallback } from 'react';

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
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  const createEmber = useCallback((canvas: HTMLCanvasElement, startFromBottom = true): Ember => {
    return {
      x: Math.random() * canvas.width,
      y: startFromBottom ? canvas.height + 10 : Math.random() * canvas.height,
      size: 1.5 + Math.random() * 3,
      speed: 0.4 + Math.random() * 0.8,
      wobblePhase: Math.random() * Math.PI * 2,
      wobbleSpeed: 1 + Math.random() * 2,
      opacity: 0.5 + Math.random() * 0.4,
      fadeSpeed: 0.002 + Math.random() * 0.003,
      glowSize: 8 + Math.random() * 12,
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

    const emberCount = Math.floor((canvas.width * canvas.height) / 25000);
    embersRef.current = Array.from({ length: Math.max(20, emberCount) }, () =>
      createEmber(canvas, false)
    );

    const animate = () => {
      if (!canvas || !ctx) return;

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

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [canvasRef, opacity, createEmber, active]);
}
