/**
 * Ink Drops Animation
 * Abstract ink slowly diffusing in water
 */

import { useRef, useEffect, useCallback } from 'react';

interface InkDrop {
  x: number;
  y: number;
  radius: number;
  maxRadius: number;
  opacity: number;
  color: { r: number; g: number; b: number };
  tendrils: Array<{
    angle: number;
    length: number;
    speed: number;
    wobble: number;
  }>;
  age: number;
  fadeStart: number;
}

export function useInkDrops(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const dropsRef = useRef<InkDrop[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);
  const lastDropTimeRef = useRef<number>(0);

  const inkColors = darkMode
    ? [
        { r: 100, g: 140, b: 180 },
        { r: 130, g: 100, b: 160 },
        { r: 80, g: 120, b: 140 },
      ]
    : [
        { r: 60, g: 80, b: 120 },
        { r: 90, g: 60, b: 110 },
        { r: 40, g: 80, b: 100 },
      ];

  const createDrop = useCallback((canvas: HTMLCanvasElement): InkDrop => {
    const tendrilCount = 4 + Math.floor(Math.random() * 4);
    return {
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      radius: 0,
      maxRadius: 40 + Math.random() * 80,
      opacity: 0.08 + Math.random() * 0.08,
      color: inkColors[Math.floor(Math.random() * inkColors.length)],
      tendrils: Array.from({ length: tendrilCount }, () => ({
        angle: Math.random() * Math.PI * 2,
        length: 0.3 + Math.random() * 0.7,
        speed: 0.5 + Math.random() * 0.5,
        wobble: Math.random() * Math.PI * 2,
      })),
      age: 0,
      fadeStart: 0.6 + Math.random() * 0.2,
    };
  }, [inkColors]);

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

    dropsRef.current = Array.from({ length: 3 }, () => createDrop(canvas));

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      if (timeRef.current - lastDropTimeRef.current > 2.5 + Math.random() * 2) {
        if (dropsRef.current.length < 8) {
          dropsRef.current.push(createDrop(canvas));
        }
        lastDropTimeRef.current = timeRef.current;
      }

      dropsRef.current.forEach((drop) => {
        drop.age += 0.008;
        const progress = Math.min(drop.age, 1);
        drop.radius = drop.maxRadius * Math.sqrt(progress);

        let currentOpacity = drop.opacity;
        if (progress > drop.fadeStart) {
          const fadeProgress = (progress - drop.fadeStart) / (1 - drop.fadeStart);
          currentOpacity *= 1 - fadeProgress;
        }

        const { r, g, b } = drop.color;

        const gradient = ctx.createRadialGradient(
          drop.x, drop.y, 0,
          drop.x, drop.y, drop.radius
        );
        gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${currentOpacity * opacityMultiplier})`);
        gradient.addColorStop(0.4, `rgba(${r}, ${g}, ${b}, ${currentOpacity * 0.6 * opacityMultiplier})`);
        gradient.addColorStop(0.7, `rgba(${r}, ${g}, ${b}, ${currentOpacity * 0.3 * opacityMultiplier})`);
        gradient.addColorStop(1, 'transparent');

        ctx.beginPath();
        ctx.arc(drop.x, drop.y, drop.radius, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();

        drop.tendrils.forEach((tendril) => {
          const tendrilLength = drop.radius * tendril.length * progress;
          const wobbleOffset = Math.sin(timeRef.current * tendril.speed + tendril.wobble) * 10;

          const endX = drop.x + Math.cos(tendril.angle) * tendrilLength + Math.cos(tendril.angle + Math.PI / 2) * wobbleOffset;
          const endY = drop.y + Math.sin(tendril.angle) * tendrilLength + Math.sin(tendril.angle + Math.PI / 2) * wobbleOffset;

          const tendrilGradient = ctx.createLinearGradient(drop.x, drop.y, endX, endY);
          tendrilGradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${currentOpacity * 0.5 * opacityMultiplier})`);
          tendrilGradient.addColorStop(1, 'transparent');

          ctx.beginPath();
          ctx.moveTo(drop.x, drop.y);
          ctx.quadraticCurveTo(
            (drop.x + endX) / 2 + wobbleOffset,
            (drop.y + endY) / 2 + wobbleOffset,
            endX,
            endY
          );
          ctx.strokeStyle = tendrilGradient;
          ctx.lineWidth = 3 + Math.random() * 2;
          ctx.lineCap = 'round';
          ctx.stroke();
        });
      });

      dropsRef.current = dropsRef.current.filter(drop => drop.age < 1);

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [canvasRef, darkMode, opacity, active, createDrop]);
}
