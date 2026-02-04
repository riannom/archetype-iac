/**
 * Snowfall Animation
 * Gentle falling snowflakes with crystal patterns
 */

import { useRef, useEffect, useCallback } from 'react';

interface Snowflake {
  x: number;
  y: number;
  size: number;
  speed: number;
  opacity: number;
  wobblePhase: number;
  wobbleSpeed: number;
  rotation: number;
  rotationSpeed: number;
  variant: number;
}

export function useSnowfall(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const snowflakesRef = useRef<Snowflake[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  const createSnowflake = useCallback((canvas: HTMLCanvasElement, startFromTop = true): Snowflake => {
    return {
      x: Math.random() * canvas.width,
      y: startFromTop ? -10 : Math.random() * canvas.height,
      size: 2 + Math.random() * 6,
      speed: 0.3 + Math.random() * 0.7,
      opacity: 0.3 + Math.random() * 0.5,
      wobblePhase: Math.random() * Math.PI * 2,
      wobbleSpeed: 0.5 + Math.random() * 1,
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.02,
      variant: Math.floor(Math.random() * 3),
    };
  }, []);

  const drawSnowflake = useCallback((
    ctx: CanvasRenderingContext2D,
    flake: Snowflake,
    isDark: boolean,
    opacityMultiplier: number
  ) => {
    ctx.save();
    ctx.translate(flake.x, flake.y);
    ctx.rotate(flake.rotation);

    const alpha = flake.opacity * opacityMultiplier;
    const color = isDark
      ? `rgba(255, 255, 255, ${alpha})`
      : `rgba(180, 200, 220, ${alpha})`;

    ctx.fillStyle = color;
    ctx.strokeStyle = color;

    const s = flake.size;

    switch (flake.variant) {
      case 0:
        ctx.beginPath();
        ctx.arc(0, 0, s / 2, 0, Math.PI * 2);
        ctx.fill();
        break;

      case 1:
        ctx.lineWidth = s * 0.15;
        ctx.lineCap = 'round';
        for (let i = 0; i < 6; i++) {
          const angle = (Math.PI / 3) * i;
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(Math.cos(angle) * s, Math.sin(angle) * s);
          ctx.stroke();
        }
        break;

      case 2:
        ctx.lineWidth = s * 0.12;
        ctx.lineCap = 'round';
        for (let i = 0; i < 6; i++) {
          const angle = (Math.PI / 3) * i;
          const cos = Math.cos(angle);
          const sin = Math.sin(angle);

          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(cos * s, sin * s);
          ctx.stroke();

          const branchLen = s * 0.4;
          const branchPos = s * 0.6;
          ctx.beginPath();
          ctx.moveTo(cos * branchPos, sin * branchPos);
          ctx.lineTo(
            cos * branchPos + Math.cos(angle + 0.5) * branchLen,
            sin * branchPos + Math.sin(angle + 0.5) * branchLen
          );
          ctx.stroke();
          ctx.beginPath();
          ctx.moveTo(cos * branchPos, sin * branchPos);
          ctx.lineTo(
            cos * branchPos + Math.cos(angle - 0.5) * branchLen,
            sin * branchPos + Math.sin(angle - 0.5) * branchLen
          );
          ctx.stroke();
        }
        break;
    }

    ctx.restore();
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

    const flakeCount = Math.floor((canvas.width * canvas.height) / 15000);
    snowflakesRef.current = Array.from({ length: flakeCount }, () =>
      createSnowflake(canvas, false)
    );

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      snowflakesRef.current.forEach((flake, index) => {
        flake.y += flake.speed;
        flake.x += Math.sin(timeRef.current * flake.wobbleSpeed + flake.wobblePhase) * 0.3;
        flake.rotation += flake.rotationSpeed;

        if (flake.y > canvas.height + 20 || flake.x < -20 || flake.x > canvas.width + 20) {
          snowflakesRef.current[index] = createSnowflake(canvas, true);
        }

        drawSnowflake(ctx, flake, darkMode, opacityMultiplier);
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
  }, [canvasRef, darkMode, opacity, createSnowflake, drawSnowflake, active]);
}
