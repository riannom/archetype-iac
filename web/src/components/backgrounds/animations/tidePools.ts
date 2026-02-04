/**
 * Tide Pools Animation
 * Gentle ripples and caustic highlights in tidal pools
 */

import { useRef, useEffect } from 'react';

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
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Teal/aqua colors
    const colorSchemes = darkMode
      ? [
          [130, 200, 200],
          [140, 190, 195],
          [120, 185, 190],
        ]
      : [
          [80, 160, 165],
          [90, 150, 160],
          [70, 145, 155],
        ];

    const createRipple = (): TideRipple => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      radius: 0,
      maxRadius: 40 + Math.random() * 60,
      speed: 0.3 + Math.random() * 0.3,
      opacity: 0.15 + Math.random() * 0.1,
      colorScheme: Math.floor(Math.random() * 3),
    });

    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;

      const rippleCount = Math.floor((canvas.width * canvas.height) / 80000) + 5;
      ripplesRef.current = Array.from({ length: rippleCount }, () => {
        const ripple = createRipple();
        ripple.radius = Math.random() * ripple.maxRadius;
        return ripple;
      });
    };
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const drawRipple = (ripple: TideRipple) => {
      const opacityMultiplier = opacity / 50;
      const colors = colorSchemes[ripple.colorScheme];
      const fadeOut = 1 - ripple.radius / ripple.maxRadius;

      ctx.save();

      // Draw concentric ripples
      for (let i = 0; i < 3; i++) {
        const r = ripple.radius - i * 8;
        if (r > 0) {
          ctx.beginPath();
          ctx.arc(ripple.x, ripple.y, r, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(${colors[0]}, ${colors[1]}, ${colors[2]}, ${ripple.opacity * opacityMultiplier * fadeOut * (1 - i * 0.25)})`;
          ctx.lineWidth = 2.5 - i * 0.5;
          ctx.stroke();
        }
      }

      // Caustic highlight
      if (ripple.radius > 10) {
        const highlightAngle = timeRef.current * 0.5 + ripple.x * 0.01;
        const hx = ripple.x + Math.cos(highlightAngle) * ripple.radius * 0.3;
        const hy = ripple.y + Math.sin(highlightAngle) * ripple.radius * 0.3;

        ctx.beginPath();
        ctx.arc(hx, hy, 4, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${ripple.opacity * opacityMultiplier * fadeOut * 0.5})`;
        ctx.fill();
      }

      ctx.restore();
    };

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      ripplesRef.current.forEach((ripple, i) => {
        ripple.radius += ripple.speed;

        if (ripple.radius >= ripple.maxRadius) {
          ripplesRef.current[i] = createRipple();
        }

        drawRipple(ripple);
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
  }, [canvasRef, darkMode, opacity, active]);
}
