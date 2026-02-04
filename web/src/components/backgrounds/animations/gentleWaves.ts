/**
 * Gentle Waves Animation (replaces noise)
 *
 * Soft, flowing wave patterns with calming ocean colors.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface WaveLayer {
  amplitude: number;
  frequency: number;
  speed: number;
  phase: number;
  yOffset: number;
  color: string;
}

interface Bubble {
  x: number;
  y: number;
  size: number;
  speed: number;
  wobblePhase: number;
  opacity: number;
}

interface Sparkle {
  x: number;
  y: number;
  phase: number;
  speed: number;
  size: number;
}

export function useGentleWaves(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const wavesRef = useRef<WaveLayer[]>([]);
  const bubblesRef = useRef<Bubble[]>([]);
  const sparklesRef = useRef<Sparkle[]>([]);
  const animationRef = useRef<number>(0);
  const timeRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeElements();
    };

    const initializeElements = () => {
      const height = canvas.height;

      // Create wave layers
      wavesRef.current = [];

      const waveConfigs = darkMode
        ? [
            { yOffset: 0.3, amplitude: 30, frequency: 0.008, speed: 0.0008, color: 'rgba(30, 60, 80, 0.4)' },
            { yOffset: 0.4, amplitude: 25, frequency: 0.01, speed: 0.001, color: 'rgba(40, 70, 90, 0.5)' },
            { yOffset: 0.5, amplitude: 20, frequency: 0.012, speed: 0.0012, color: 'rgba(50, 80, 100, 0.6)' },
            { yOffset: 0.6, amplitude: 18, frequency: 0.015, speed: 0.0015, color: 'rgba(60, 90, 110, 0.7)' },
            { yOffset: 0.7, amplitude: 15, frequency: 0.018, speed: 0.0018, color: 'rgba(70, 100, 120, 0.8)' },
          ]
        : [
            { yOffset: 0.3, amplitude: 30, frequency: 0.008, speed: 0.0008, color: 'rgba(100, 180, 220, 0.3)' },
            { yOffset: 0.4, amplitude: 25, frequency: 0.01, speed: 0.001, color: 'rgba(80, 160, 210, 0.4)' },
            { yOffset: 0.5, amplitude: 20, frequency: 0.012, speed: 0.0012, color: 'rgba(60, 140, 200, 0.5)' },
            { yOffset: 0.6, amplitude: 18, frequency: 0.015, speed: 0.0015, color: 'rgba(40, 120, 190, 0.6)' },
            { yOffset: 0.7, amplitude: 15, frequency: 0.018, speed: 0.0018, color: 'rgba(20, 100, 180, 0.7)' },
          ];

      waveConfigs.forEach((config) => {
        wavesRef.current.push({
          amplitude: config.amplitude,
          frequency: config.frequency,
          speed: config.speed,
          phase: Math.random() * Math.PI * 2,
          yOffset: config.yOffset * height,
          color: config.color,
        });
      });

      // Create bubbles
      bubblesRef.current = [];
      for (let i = 0; i < 15; i++) {
        bubblesRef.current.push(createBubble());
      }

      // Create surface sparkles
      sparklesRef.current = [];
      for (let i = 0; i < 20; i++) {
        sparklesRef.current.push({
          x: Math.random() * canvas.width,
          y: height * 0.25 + Math.random() * height * 0.15,
          phase: Math.random() * Math.PI * 2,
          speed: 0.03 + Math.random() * 0.03,
          size: 2 + Math.random() * 3,
        });
      }
    };

    const createBubble = (): Bubble => ({
      x: Math.random() * canvas.width,
      y: canvas.height + 20 + Math.random() * 50,
      size: 3 + Math.random() * 8,
      speed: 0.3 + Math.random() * 0.4,
      wobblePhase: Math.random() * Math.PI * 2,
      opacity: 0.2 + Math.random() * 0.3,
    });

    const drawBackground = () => {
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
      if (darkMode) {
        gradient.addColorStop(0, '#0a1520');
        gradient.addColorStop(0.3, '#102030');
        gradient.addColorStop(0.6, '#152535');
        gradient.addColorStop(1, '#0a1015');
      } else {
        gradient.addColorStop(0, '#E0F4FF');
        gradient.addColorStop(0.3, '#B0E0FF');
        gradient.addColorStop(0.6, '#80C8F0');
        gradient.addColorStop(1, '#4090C0');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    };

    const drawWaveLayer = (wave: WaveLayer, time: number) => {
      ctx.beginPath();
      ctx.moveTo(0, canvas.height);

      // Calculate wave points with multiple harmonics for fluid motion
      const points: { x: number; y: number }[] = [];
      const step = 3; // Smaller steps for smoother curves

      for (let x = 0; x <= canvas.width + step; x += step) {
        // Primary wave
        const primary = Math.sin(x * wave.frequency + time * wave.speed + wave.phase) * wave.amplitude;
        // Secondary harmonic (slower, longer wavelength)
        const secondary = Math.sin(x * wave.frequency * 0.5 + time * wave.speed * 0.6 + wave.phase * 1.3) * wave.amplitude * 0.4;
        // Tertiary harmonic (faster ripples)
        const tertiary = Math.sin(x * wave.frequency * 2.3 + time * wave.speed * 1.4 + wave.phase * 0.7) * wave.amplitude * 0.15;
        // Slow drift for organic feel
        const drift = Math.sin(x * wave.frequency * 0.2 + time * wave.speed * 0.3) * wave.amplitude * 0.2;

        const y = wave.yOffset + primary + secondary + tertiary + drift;
        points.push({ x, y });
      }

      // Draw using quadratic curves for fluid appearance
      ctx.moveTo(0, points[0].y);

      for (let i = 0; i < points.length - 1; i++) {
        const curr = points[i];
        const next = points[i + 1];
        // Control point at midpoint for smooth curves
        const cpX = (curr.x + next.x) / 2;
        const cpY = (curr.y + next.y) / 2;
        ctx.quadraticCurveTo(curr.x, curr.y, cpX, cpY);
      }

      // Complete the last segment
      const last = points[points.length - 1];
      ctx.lineTo(last.x, last.y);
      ctx.lineTo(canvas.width, canvas.height);
      ctx.lineTo(0, canvas.height);
      ctx.closePath();
      ctx.fillStyle = wave.color;
      ctx.fill();
    };

    const drawBubble = (bubble: Bubble, time: number) => {
      bubble.y -= bubble.speed;
      bubble.wobblePhase += 0.02;
      const wobble = Math.sin(bubble.wobblePhase) * 2;
      bubble.x += wobble * 0.1;

      if (bubble.y < canvas.height * 0.25) {
        Object.assign(bubble, createBubble());
        return;
      }

      ctx.beginPath();
      ctx.arc(bubble.x + wobble, bubble.y, bubble.size, 0, Math.PI * 2);

      // Bubble gradient
      const bubbleGradient = ctx.createRadialGradient(
        bubble.x + wobble - bubble.size * 0.3,
        bubble.y - bubble.size * 0.3,
        0,
        bubble.x + wobble,
        bubble.y,
        bubble.size
      );
      bubbleGradient.addColorStop(0, `rgba(255, 255, 255, ${bubble.opacity * 0.5})`);
      bubbleGradient.addColorStop(0.7, `rgba(200, 230, 255, ${bubble.opacity * 0.2})`);
      bubbleGradient.addColorStop(1, `rgba(150, 200, 255, ${bubble.opacity * 0.1})`);

      ctx.fillStyle = bubbleGradient;
      ctx.fill();

      // Highlight
      ctx.beginPath();
      ctx.arc(
        bubble.x + wobble - bubble.size * 0.3,
        bubble.y - bubble.size * 0.3,
        bubble.size * 0.2,
        0,
        Math.PI * 2
      );
      ctx.fillStyle = `rgba(255, 255, 255, ${bubble.opacity * 0.6})`;
      ctx.fill();
    };

    const drawSparkle = (sparkle: Sparkle, time: number) => {
      sparkle.phase += sparkle.speed;
      const intensity = Math.sin(sparkle.phase);

      if (intensity > 0) {
        ctx.beginPath();
        ctx.arc(sparkle.x, sparkle.y, sparkle.size * intensity, 0, Math.PI * 2);
        ctx.fillStyle = darkMode
          ? `rgba(200, 220, 255, ${intensity * 0.4})`
          : `rgba(255, 255, 255, ${intensity * 0.8})`;
        ctx.fill();
      }
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawBackground();

      // Draw waves back to front
      wavesRef.current.forEach((wave) => drawWaveLayer(wave, time));

      // Draw bubbles
      bubblesRef.current.forEach((bubble) => drawBubble(bubble, time));

      // Draw sparkles
      sparklesRef.current.forEach((sparkle) => drawSparkle(sparkle, time));

      animationRef.current = requestAnimationFrame(animate);
    };

    resize();
    window.addEventListener('resize', resize);
    animate();

    return () => {
      window.removeEventListener('resize', resize);
      cancelAnimationFrame(animationRef.current);
    };
  }, [canvasRef, darkMode, opacity, active]);
}
