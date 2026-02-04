/**
 * Raindrop Window Animation
 *
 * Raindrops rolling down a window pane with a cozy blurred background.
 * Drops merge, trail, and create a soothing rainy day atmosphere.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface Raindrop {
  x: number;
  y: number;
  size: number;
  speed: number;
  wobblePhase: number;
  wobbleSpeed: number;
  trail: { x: number; y: number; size: number }[];
  maxTrailLength: number;
}

interface BackgroundLight {
  x: number;
  y: number;
  size: number;
  hue: number;
  brightness: number;
  pulsePhase: number;
}

interface StreamLine {
  x: number;
  startY: number;
  segments: { y: number; offset: number }[];
  opacity: number;
}

export function useRaindropWindow(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const dropsRef = useRef<Raindrop[]>([]);
  const lightsRef = useRef<BackgroundLight[]>([]);
  const streamsRef = useRef<StreamLine[]>([]);
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
      const width = canvas.width;
      const height = canvas.height;

      // Create background bokeh lights
      lightsRef.current = [];
      for (let i = 0; i < 15; i++) {
        lightsRef.current.push({
          x: Math.random() * width,
          y: Math.random() * height,
          size: 30 + Math.random() * 80,
          hue: Math.random() > 0.5 ? 40 + Math.random() * 20 : 200 + Math.random() * 40,
          brightness: 0.3 + Math.random() * 0.4,
          pulsePhase: Math.random() * Math.PI * 2,
        });
      }

      // Create raindrops
      dropsRef.current = [];
      for (let i = 0; i < 25; i++) {
        dropsRef.current.push(createDrop(width, height, true));
      }

      // Create stream lines (water trails that stay)
      streamsRef.current = [];
      for (let i = 0; i < 8; i++) {
        const segments: { y: number; offset: number }[] = [];
        let y = 0;
        while (y < height) {
          segments.push({
            y,
            offset: (Math.random() - 0.5) * 10,
          });
          y += 20 + Math.random() * 30;
        }
        streamsRef.current.push({
          x: Math.random() * width,
          startY: 0,
          segments,
          opacity: 0.1 + Math.random() * 0.15,
        });
      }
    };

    const createDrop = (width: number, height: number, randomY: boolean): Raindrop => ({
      x: Math.random() * width,
      y: randomY ? Math.random() * height : -20 - Math.random() * 50,
      size: 4 + Math.random() * 8,
      speed: 0.5 + Math.random() * 1,
      wobblePhase: Math.random() * Math.PI * 2,
      wobbleSpeed: 0.02 + Math.random() * 0.03,
      trail: [],
      maxTrailLength: 5 + Math.floor(Math.random() * 10),
    });

    const drawBackground = () => {
      // Rainy window background
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
      if (darkMode) {
        gradient.addColorStop(0, '#1a1a25');
        gradient.addColorStop(0.5, '#15152a');
        gradient.addColorStop(1, '#101020');
      } else {
        gradient.addColorStop(0, '#4a5568');
        gradient.addColorStop(0.5, '#3d4a5c');
        gradient.addColorStop(1, '#2d3748');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    };

    const drawBokehLights = (time: number) => {
      lightsRef.current.forEach((light) => {
        const pulse = Math.sin(time * 0.001 + light.pulsePhase) * 0.15 + 0.85;
        const gradient = ctx.createRadialGradient(
          light.x,
          light.y,
          0,
          light.x,
          light.y,
          light.size
        );

        const alpha = light.brightness * pulse * (darkMode ? 0.3 : 0.25);
        gradient.addColorStop(0, `hsla(${light.hue}, 60%, 70%, ${alpha})`);
        gradient.addColorStop(0.5, `hsla(${light.hue}, 50%, 60%, ${alpha * 0.5})`);
        gradient.addColorStop(1, 'transparent');

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(light.x, light.y, light.size, 0, Math.PI * 2);
        ctx.fill();
      });
    };

    const drawStreamLines = () => {
      streamsRef.current.forEach((stream) => {
        ctx.beginPath();
        ctx.moveTo(stream.x + stream.segments[0].offset, 0);

        stream.segments.forEach((segment) => {
          ctx.lineTo(stream.x + segment.offset, segment.y);
        });

        ctx.strokeStyle = darkMode
          ? `rgba(120, 140, 180, ${stream.opacity})`
          : `rgba(180, 200, 220, ${stream.opacity})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      });
    };

    const drawRaindrop = (drop: Raindrop, time: number) => {
      // Update position
      const wobble = Math.sin(time * drop.wobbleSpeed + drop.wobblePhase) * 0.5;
      drop.x += wobble * 0.1;
      drop.y += drop.speed;

      // Add to trail
      if (Math.random() > 0.7) {
        drop.trail.push({
          x: drop.x,
          y: drop.y,
          size: drop.size * 0.3,
        });
        if (drop.trail.length > drop.maxTrailLength) {
          drop.trail.shift();
        }
      }

      // Draw trail
      drop.trail.forEach((point, index) => {
        const trailOpacity = (index / drop.trail.length) * 0.18;
        ctx.beginPath();
        ctx.arc(point.x, point.y, point.size, 0, Math.PI * 2);
        ctx.fillStyle = darkMode
          ? `rgba(150, 180, 220, ${trailOpacity})`
          : `rgba(200, 220, 240, ${trailOpacity})`;
        ctx.fill();
      });

      // Draw main drop
      ctx.save();
      ctx.translate(drop.x, drop.y);

      // Drop shadow/refraction effect
      const shadowGradient = ctx.createRadialGradient(
        drop.size * 0.2,
        drop.size * 0.2,
        0,
        0,
        0,
        drop.size * 1.5
      );
      shadowGradient.addColorStop(0, 'rgba(0, 0, 0, 0.1)');
      shadowGradient.addColorStop(1, 'transparent');
      ctx.fillStyle = shadowGradient;
      ctx.beginPath();
      ctx.arc(drop.size * 0.2, drop.size * 0.2, drop.size * 1.5, 0, Math.PI * 2);
      ctx.fill();

      // Main drop body
      const dropGradient = ctx.createRadialGradient(
        -drop.size * 0.3,
        -drop.size * 0.3,
        0,
        0,
        0,
        drop.size
      );
      if (darkMode) {
        dropGradient.addColorStop(0, 'rgba(180, 200, 230, 0.35)');
        dropGradient.addColorStop(0.5, 'rgba(140, 160, 200, 0.2)');
        dropGradient.addColorStop(1, 'rgba(100, 120, 160, 0.08)');
      } else {
        dropGradient.addColorStop(0, 'rgba(220, 235, 250, 0.4)');
        dropGradient.addColorStop(0.5, 'rgba(180, 200, 230, 0.22)');
        dropGradient.addColorStop(1, 'rgba(140, 160, 200, 0.08)');
      }

      // Elongated drop shape
      ctx.beginPath();
      ctx.ellipse(0, 0, drop.size * 0.8, drop.size, 0, 0, Math.PI * 2);
      ctx.fillStyle = dropGradient;
      ctx.fill();

      // Highlight
      ctx.beginPath();
      ctx.arc(-drop.size * 0.3, -drop.size * 0.3, drop.size * 0.25, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
      ctx.fill();

      ctx.restore();
    };

    const drawWindowFrame = () => {
      // Subtle window frame edges
      const frameWidth = 20;
      const gradient = ctx.createLinearGradient(0, 0, frameWidth, 0);
      gradient.addColorStop(0, darkMode ? 'rgba(30, 30, 40, 0.8)' : 'rgba(60, 60, 70, 0.6)');
      gradient.addColorStop(1, 'transparent');

      // Left edge
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, frameWidth, canvas.height);

      // Right edge
      ctx.save();
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
      ctx.fillRect(0, 0, frameWidth, canvas.height);
      ctx.restore();

      // Top condensation effect
      const topGradient = ctx.createLinearGradient(0, 0, 0, 100);
      topGradient.addColorStop(0, darkMode ? 'rgba(80, 100, 130, 0.2)' : 'rgba(150, 170, 200, 0.15)');
      topGradient.addColorStop(1, 'transparent');
      ctx.fillStyle = topGradient;
      ctx.fillRect(0, 0, canvas.width, 100);
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawBackground();
      drawBokehLights(time);
      drawStreamLines();

      // Update and draw drops
      dropsRef.current.forEach((drop, index) => {
        drawRaindrop(drop, time);

        // Reset if off screen
        if (drop.y > canvas.height + 50) {
          dropsRef.current[index] = createDrop(canvas.width, canvas.height, false);
        }
      });

      drawWindowFrame();

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
