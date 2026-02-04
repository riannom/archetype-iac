/**
 * Dandelion Wishes Animation (replaces polka dots)
 *
 * Fluffy dandelion seeds floating gently on the breeze.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface DandelionSeed {
  x: number;
  y: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
  vx: number;
  vy: number;
  wobblePhase: number;
  opacity: number;
  fluffCount: number;
  fluffAngles: number[];
}

interface Dandelion {
  x: number;
  y: number;
  stemHeight: number;
  seedCount: number;
  phase: number;
}

export function useDandelionWishes(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const seedsRef = useRef<DandelionSeed[]>([]);
  const dandelionsRef = useRef<Dandelion[]>([]);
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

    const createSeed = (width: number, height: number, fromDandelion?: { x: number; y: number }): DandelionSeed => {
      const fluffCount = 8 + Math.floor(Math.random() * 6);
      const fluffAngles: number[] = [];
      for (let i = 0; i < fluffCount; i++) {
        fluffAngles.push((i / fluffCount) * Math.PI * 2 + (Math.random() - 0.5) * 0.3);
      }

      return {
        x: fromDandelion ? fromDandelion.x : Math.random() * width,
        y: fromDandelion ? fromDandelion.y : -20 - Math.random() * 100,
        size: 8 + Math.random() * 6,
        rotation: Math.random() * Math.PI * 2,
        rotationSpeed: (Math.random() - 0.5) * 0.01,
        vx: (Math.random() - 0.5) * 0.5,
        vy: 0.2 + Math.random() * 0.3,
        wobblePhase: Math.random() * Math.PI * 2,
        opacity: 0.6 + Math.random() * 0.4,
        fluffCount,
        fluffAngles,
      };
    };

    const initializeElements = () => {
      const width = canvas.width;
      const height = canvas.height;

      // Create floating seeds
      seedsRef.current = [];
      for (let i = 0; i < 30; i++) {
        const seed = createSeed(width, height);
        seed.y = Math.random() * height; // Start scattered
        seedsRef.current.push(seed);
      }

      // Create dandelion plants
      dandelionsRef.current = [];
      for (let i = 0; i < 4; i++) {
        dandelionsRef.current.push({
          x: width * 0.1 + Math.random() * width * 0.8,
          y: height * 0.85 + Math.random() * height * 0.1,
          stemHeight: 80 + Math.random() * 60,
          seedCount: 20 + Math.floor(Math.random() * 15),
          phase: Math.random() * Math.PI * 2,
        });
      }
    };

    const drawBackground = () => {
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
      if (darkMode) {
        gradient.addColorStop(0, '#1a2030');
        gradient.addColorStop(0.7, '#253040');
        gradient.addColorStop(1, '#1a3020');
      } else {
        gradient.addColorStop(0, '#E0F7FA');
        gradient.addColorStop(0.7, '#B2EBF2');
        gradient.addColorStop(1, '#81C784');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    };

    const drawDandelion = (dandelion: Dandelion, time: number) => {
      const sway = Math.sin(time * 0.001 + dandelion.phase) * 5;

      ctx.save();
      ctx.translate(dandelion.x, dandelion.y);

      // Stem
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.quadraticCurveTo(sway, -dandelion.stemHeight / 2, sway * 0.5, -dandelion.stemHeight);
      ctx.strokeStyle = darkMode ? '#3a5a3a' : '#66BB6A';
      ctx.lineWidth = 3;
      ctx.stroke();

      // Seed head
      ctx.translate(sway * 0.5, -dandelion.stemHeight);

      // Draw remaining seeds on head
      for (let i = 0; i < dandelion.seedCount; i++) {
        const angle = (i / dandelion.seedCount) * Math.PI * 2;
        const radius = 15;

        ctx.save();
        ctx.rotate(angle);
        ctx.translate(0, -radius);

        // Seed
        ctx.beginPath();
        ctx.ellipse(0, 0, 1, 3, 0, 0, Math.PI * 2);
        ctx.fillStyle = darkMode ? '#8a8a7a' : '#9E9E9E';
        ctx.fill();

        // Fluff
        ctx.strokeStyle = darkMode ? 'rgba(200, 200, 190, 0.5)' : 'rgba(255, 255, 255, 0.7)';
        ctx.lineWidth = 0.5;
        for (let f = 0; f < 6; f++) {
          const fluffAngle = (f / 6) * Math.PI - Math.PI / 2;
          ctx.beginPath();
          ctx.moveTo(0, -3);
          ctx.lineTo(Math.cos(fluffAngle) * 8, -3 + Math.sin(fluffAngle) * 8);
          ctx.stroke();
        }

        ctx.restore();
      }

      ctx.restore();
    };

    const drawSeed = (seed: DandelionSeed, time: number) => {
      // Update position
      const wobble = Math.sin(time * 0.002 + seed.wobblePhase);
      seed.x += seed.vx + wobble * 0.3;
      seed.y += seed.vy;
      seed.rotation += seed.rotationSpeed;

      // Gentle wind effect
      seed.vx += (Math.sin(time * 0.0005) * 0.01);
      seed.vx *= 0.99;

      ctx.save();
      ctx.translate(seed.x, seed.y);
      ctx.rotate(seed.rotation);
      ctx.globalAlpha = seed.opacity;

      // Seed body
      ctx.beginPath();
      ctx.ellipse(0, seed.size * 0.5, 1.5, 4, 0, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? '#7a7a6a' : '#8D6E63';
      ctx.fill();

      // Fluff (umbrella shape)
      ctx.strokeStyle = darkMode ? 'rgba(220, 220, 210, 0.6)' : 'rgba(255, 255, 255, 0.8)';
      ctx.lineWidth = 0.5;

      seed.fluffAngles.forEach((angle) => {
        const length = seed.size * 0.8;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(Math.cos(angle) * length, Math.sin(angle) * length - seed.size * 0.3);
        ctx.stroke();

        // Tiny wisps at end
        const endX = Math.cos(angle) * length;
        const endY = Math.sin(angle) * length - seed.size * 0.3;
        for (let w = 0; w < 3; w++) {
          const wispAngle = angle + (w - 1) * 0.3;
          ctx.beginPath();
          ctx.moveTo(endX, endY);
          ctx.lineTo(
            endX + Math.cos(wispAngle) * 3,
            endY + Math.sin(wispAngle) * 3 - 2
          );
          ctx.stroke();
        }
      });

      ctx.restore();
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawBackground();

      // Draw dandelions
      dandelionsRef.current.forEach((d) => drawDandelion(d, time));

      // Draw and update seeds
      seedsRef.current.forEach((seed, index) => {
        drawSeed(seed, time);

        // Reset if off screen
        if (seed.y > canvas.height + 50 || seed.x < -50 || seed.x > canvas.width + 50) {
          seedsRef.current[index] = createSeed(canvas.width, canvas.height);
        }
      });

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
