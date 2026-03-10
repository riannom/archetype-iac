/**
 * Northern Lights Animation
 * Flowing aurora bands with shifting colors
 */

import { useRef} from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

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
  const sizeRef = useRef({ w: 0, h: 0 });

  useCanvasAnimation(canvasRef, darkMode, opacity, active, {

    draw: (ctx, canvas, time, _dt) => {
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


      const w = canvas.width;
      const h = canvas.height;
      if (sizeRef.current.w !== w || sizeRef.current.h !== h) {
        sizeRef.current = { w, h };
      }


      ctx.clearRect(0, 0, canvas.width, canvas.height);
      time += 0.016;

      const opacityMultiplier = opacity / 50;

      bandsRef.current.forEach((band) => {
        const colorPhase = time * 0.1 + band.colorShift;

        // More vibrant colors with better saturation
        const r = darkMode ? 100 + Math.sin(colorPhase) * 80 : 80 + Math.sin(colorPhase) * 60;
        const g = darkMode ? 220 + Math.sin(colorPhase + 1) * 35 : 200 + Math.sin(colorPhase + 1) * 55;
        const b = darkMode ? 180 + Math.sin(colorPhase + 2) * 75 : 160 + Math.sin(colorPhase + 2) * 70;

        ctx.beginPath();

        for (let x = 0; x <= canvas.width; x += 3) {
          const wave1 = Math.sin(x * band.frequency + time * band.speed + band.phase) * band.amplitude;
          const wave2 = Math.sin(x * band.frequency * 1.5 + time * band.speed * 0.8 + band.phase + 1) * (band.amplitude * 0.5);
          const y = band.y + wave1 + wave2;

          if (x === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        }

        for (let x = canvas.width; x >= 0; x -= 3) {
          const wave1 = Math.sin(x * band.frequency + time * band.speed + band.phase) * band.amplitude;
          const wave2 = Math.sin(x * band.frequency * 1.5 + time * band.speed * 0.8 + band.phase + 1) * (band.amplitude * 0.5);
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

    },
  });
}
