/**
 * Rippling Water Animation
 * Gentle ripples appearing on calm water
 */

import { useRef, useEffect, useCallback } from 'react';

interface Ripple {
  x: number;
  y: number;
  radius: number;
  maxRadius: number;
  opacity: number;
  speed: number;
}

export function useRipplingWater(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const ripplesRef = useRef<Ripple[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);
  const lastRippleTimeRef = useRef<number>(0);

  const createRipple = useCallback((canvas: HTMLCanvasElement): Ripple => {
    return {
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      radius: 0,
      maxRadius: 80 + Math.random() * 120,
      opacity: 0.15 + Math.random() * 0.15,
      speed: 0.5 + Math.random() * 0.5,
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

    ripplesRef.current = Array.from({ length: 3 }, () => {
      const ripple = createRipple(canvas);
      ripple.radius = Math.random() * ripple.maxRadius;
      return ripple;
    });

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      if (timeRef.current - lastRippleTimeRef.current > 1 + Math.random() * 2) {
        if (ripplesRef.current.length < 8) {
          ripplesRef.current.push(createRipple(canvas));
        }
        lastRippleTimeRef.current = timeRef.current;
      }

      ripplesRef.current = ripplesRef.current.filter((ripple) => {
        ripple.radius += ripple.speed;

        const fadeRatio = 1 - ripple.radius / ripple.maxRadius;
        const alpha = ripple.opacity * fadeRatio * opacityMultiplier;

        if (alpha < 0.01) return false;

        const ringColor = darkMode
          ? { r: 150, g: 200, b: 255 }
          : { r: 100, g: 150, b: 200 };

        for (let ring = 0; ring < 3; ring++) {
          const ringRadius = ripple.radius - ring * 8;
          if (ringRadius > 0) {
            ctx.beginPath();
            ctx.arc(ripple.x, ripple.y, ringRadius, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(${ringColor.r}, ${ringColor.g}, ${ringColor.b}, ${alpha * (1 - ring * 0.3)})`;
            ctx.lineWidth = 1.5 - ring * 0.4;
            ctx.stroke();
          }
        }

        return true;
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
  }, [canvasRef, darkMode, opacity, createRipple, active]);
}
