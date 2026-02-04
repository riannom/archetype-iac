/**
 * Northern Lights Animation
 * Flowing aurora bands with shifting colors
 */

import { useRef, useEffect } from 'react';

interface AuroraBand {
  y: number;
  amplitude: number;
  frequency: number;
  phase: number;
  speed: number;
  colorShift: number;
  opacity: number;
  height: number;
}

export function useNorthernLights(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const bandsRef = useRef<AuroraBand[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

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

    bandsRef.current = Array.from({ length: 5 }, (_, i) => ({
      y: canvas.height * 0.2 + (canvas.height * 0.4 * i) / 5,
      amplitude: 30 + Math.random() * 50,
      frequency: 0.002 + Math.random() * 0.003,
      phase: Math.random() * Math.PI * 2,
      speed: 0.1 + Math.random() * 0.2,
      colorShift: Math.random() * Math.PI * 2,
      opacity: 0.12 + Math.random() * 0.08,
      height: 80 + Math.random() * 120,
    }));

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      bandsRef.current.forEach((band) => {
        const colorPhase = timeRef.current * 0.1 + band.colorShift;

        // More vibrant colors with better saturation
        const r = darkMode ? 100 + Math.sin(colorPhase) * 80 : 80 + Math.sin(colorPhase) * 60;
        const g = darkMode ? 220 + Math.sin(colorPhase + 1) * 35 : 200 + Math.sin(colorPhase + 1) * 55;
        const b = darkMode ? 180 + Math.sin(colorPhase + 2) * 75 : 160 + Math.sin(colorPhase + 2) * 70;

        ctx.beginPath();

        for (let x = 0; x <= canvas.width; x += 3) {
          const wave1 = Math.sin(x * band.frequency + timeRef.current * band.speed + band.phase) * band.amplitude;
          const wave2 = Math.sin(x * band.frequency * 1.5 + timeRef.current * band.speed * 0.8 + band.phase + 1) * (band.amplitude * 0.5);
          const y = band.y + wave1 + wave2;

          if (x === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        }

        for (let x = canvas.width; x >= 0; x -= 3) {
          const wave1 = Math.sin(x * band.frequency + timeRef.current * band.speed + band.phase) * band.amplitude;
          const wave2 = Math.sin(x * band.frequency * 1.5 + timeRef.current * band.speed * 0.8 + band.phase + 1) * (band.amplitude * 0.5);
          const y = band.y + wave1 + wave2 + band.height;
          ctx.lineTo(x, y);
        }

        ctx.closePath();

        const gradient = ctx.createLinearGradient(0, band.y - band.amplitude, 0, band.y + band.height + band.amplitude);
        gradient.addColorStop(0, 'transparent');
        gradient.addColorStop(0.3, `rgba(${r}, ${g}, ${b}, ${band.opacity * opacityMultiplier})`);
        gradient.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${band.opacity * 1.5 * opacityMultiplier})`);
        gradient.addColorStop(0.7, `rgba(${r}, ${g}, ${b}, ${band.opacity * opacityMultiplier})`);
        gradient.addColorStop(1, 'transparent');

        ctx.fillStyle = gradient;
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
  }, [canvasRef, darkMode, opacity, active]);
}
