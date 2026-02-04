/**
 * Gentle Rain Animation
 * Soft diagonal rain streaks with subtle splash effects
 */

import { useRef, useEffect } from 'react';

interface RainDrop {
  x: number;
  y: number;
  length: number;
  speed: number;
  opacity: number;
  thickness: number;
}

interface Splash {
  x: number;
  y: number;
  radius: number;
  maxRadius: number;
  opacity: number;
}

export function useGentleRain(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const dropsRef = useRef<RainDrop[]>([]);
  const splashesRef = useRef<Splash[]>([]);
  const animationRef = useRef<number | undefined>(undefined);

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

    const dropCount = Math.floor((canvas.width * canvas.height) / 15000);
    dropsRef.current = Array.from({ length: Math.max(30, dropCount) }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      length: 15 + Math.random() * 25,
      speed: 4 + Math.random() * 4,
      opacity: 0.1 + Math.random() * 0.15,
      thickness: 0.5 + Math.random() * 1,
    }));

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const opacityMultiplier = opacity / 50;

      const rainColor = darkMode
        ? { r: 180, g: 200, b: 230 }
        : { r: 120, g: 150, b: 190 };

      dropsRef.current.forEach((drop) => {
        drop.y += drop.speed;
        drop.x += drop.speed * 0.15;

        if (drop.y > canvas.height) {
          if (Math.random() < 0.3) {
            splashesRef.current.push({
              x: drop.x,
              y: canvas.height,
              radius: 0,
              maxRadius: 5 + Math.random() * 10,
              opacity: drop.opacity * 0.5,
            });
          }
          drop.y = -drop.length;
          drop.x = Math.random() * canvas.width;
        }

        const alpha = drop.opacity * opacityMultiplier;

        ctx.beginPath();
        ctx.moveTo(drop.x, drop.y);
        ctx.lineTo(drop.x + drop.length * 0.15, drop.y + drop.length);
        ctx.strokeStyle = `rgba(${rainColor.r}, ${rainColor.g}, ${rainColor.b}, ${alpha})`;
        ctx.lineWidth = drop.thickness;
        ctx.lineCap = 'round';
        ctx.stroke();
      });

      splashesRef.current = splashesRef.current.filter((splash) => {
        splash.radius += 0.5;
        const fadeRatio = 1 - splash.radius / splash.maxRadius;
        const alpha = splash.opacity * fadeRatio * opacityMultiplier;

        if (alpha < 0.01) return false;

        ctx.beginPath();
        ctx.arc(splash.x, splash.y, splash.radius, Math.PI, 0);
        ctx.strokeStyle = `rgba(${rainColor.r}, ${rainColor.g}, ${rainColor.b}, ${alpha})`;
        ctx.lineWidth = 0.5;
        ctx.stroke();

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
  }, [canvasRef, darkMode, opacity, active]);
}
