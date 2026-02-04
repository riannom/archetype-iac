/**
 * Lavender Fields Animation
 *
 * Rolling fields of lavender swaying gently in the breeze.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface LavenderStem {
  x: number;
  baseY: number;
  height: number;
  phase: number;
  swayAmount: number;
  flowerCount: number;
  flowerOffsets: { y: number; size: number; hue: number }[];
}

interface Butterfly {
  x: number;
  y: number;
  vx: number;
  vy: number;
  wingPhase: number;
  wingSpeed: number;
  hue: number;
  size: number;
  targetX: number;
  targetY: number;
  restTimer: number;
  isResting: boolean;
  bodyAngle: number;
  flutterOffset: number;
}

interface Cloud {
  x: number;
  y: number;
  width: number;
  opacity: number;
  speed: number;
  puffs: { offsetX: number; offsetY: number; size: number }[];
}

export function useLavenderFields(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const stemsRef = useRef<LavenderStem[]>([]);
  const butterfliesRef = useRef<Butterfly[]>([]);
  const cloudsRef = useRef<Cloud[]>([]);
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

      // Create lavender stems - denser at the bottom
      stemsRef.current = [];
      const stemCount = Math.floor(width / 8);
      for (let i = 0; i < stemCount; i++) {
        const x = (i / stemCount) * width + (Math.random() - 0.5) * 15;
        const row = Math.floor(Math.random() * 5);
        const baseY = height * 0.5 + row * (height * 0.1);
        const heightVariation = 40 + Math.random() * 60;
        const flowerCount = 3 + Math.floor(Math.random() * 4);

        const flowerOffsets: { y: number; size: number; hue: number }[] = [];
        for (let f = 0; f < flowerCount; f++) {
          flowerOffsets.push({
            y: (f / flowerCount) * heightVariation * 0.6,
            size: 2 + Math.random() * 2,
            hue: 260 + Math.random() * 20 - 10, // Purple variations
          });
        }

        stemsRef.current.push({
          x,
          baseY,
          height: heightVariation,
          phase: Math.random() * Math.PI * 2,
          swayAmount: 0.02 + Math.random() * 0.02,
          flowerCount,
          flowerOffsets,
        });
      }

      // Create butterflies
      butterfliesRef.current = [];
      const butterflyCount = 3 + Math.floor(Math.random() * 3);
      for (let i = 0; i < butterflyCount; i++) {
        butterfliesRef.current.push({
          x: Math.random() * width,
          y: height * 0.3 + Math.random() * height * 0.4,
          vx: 0,
          vy: 0,
          wingPhase: Math.random() * Math.PI * 2,
          wingSpeed: 0.04 + Math.random() * 0.02, // Slower, varied wing speed
          hue: Math.random() > 0.5 ? 40 : 30, // Orange or yellow
          size: 8 + Math.random() * 6,
          targetX: Math.random() * width,
          targetY: height * 0.3 + Math.random() * height * 0.4,
          restTimer: 0,
          isResting: false,
          bodyAngle: 0,
          flutterOffset: Math.random() * Math.PI * 2,
        });
      }

      // Create clouds
      cloudsRef.current = [];
      for (let i = 0; i < 4; i++) {
        const puffs: { offsetX: number; offsetY: number; size: number }[] = [];
        const puffCount = 4 + Math.floor(Math.random() * 3);
        for (let p = 0; p < puffCount; p++) {
          puffs.push({
            offsetX: (p - puffCount / 2) * 30 + (Math.random() - 0.5) * 20,
            offsetY: (Math.random() - 0.5) * 20,
            size: 25 + Math.random() * 20,
          });
        }
        cloudsRef.current.push({
          x: Math.random() * width * 1.5,
          y: 30 + Math.random() * height * 0.15,
          width: 80 + Math.random() * 60,
          opacity: 0.3 + Math.random() * 0.3,
          speed: 0.1 + Math.random() * 0.15,
          puffs,
        });
      }
    };

    const drawSky = () => {
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
      if (darkMode) {
        gradient.addColorStop(0, '#1a1a2e');
        gradient.addColorStop(0.5, '#2d2d44');
        gradient.addColorStop(1, '#3d3d5c');
      } else {
        gradient.addColorStop(0, '#87CEEB');
        gradient.addColorStop(0.5, '#B0E0E6');
        gradient.addColorStop(1, '#E6E6FA');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    };

    const drawHills = () => {
      const height = canvas.height;
      const width = canvas.width;

      // Distant hills
      ctx.beginPath();
      ctx.moveTo(0, height * 0.5);
      for (let x = 0; x <= width; x += 50) {
        const y = height * 0.45 + Math.sin(x * 0.005) * 30 + Math.sin(x * 0.01) * 15;
        ctx.lineTo(x, y);
      }
      ctx.lineTo(width, height);
      ctx.lineTo(0, height);
      ctx.closePath();
      ctx.fillStyle = darkMode ? '#4a4a6a' : '#9370DB';
      ctx.fill();

      // Middle hills
      ctx.beginPath();
      ctx.moveTo(0, height * 0.55);
      for (let x = 0; x <= width; x += 50) {
        const y = height * 0.52 + Math.sin(x * 0.008 + 1) * 25 + Math.sin(x * 0.012) * 12;
        ctx.lineTo(x, y);
      }
      ctx.lineTo(width, height);
      ctx.lineTo(0, height);
      ctx.closePath();
      ctx.fillStyle = darkMode ? '#5a5a7a' : '#8B7B8B';
      ctx.fill();

      // Foreground field
      ctx.beginPath();
      ctx.moveTo(0, height * 0.6);
      for (let x = 0; x <= width; x += 30) {
        const y = height * 0.58 + Math.sin(x * 0.01 + 2) * 15;
        ctx.lineTo(x, y);
      }
      ctx.lineTo(width, height);
      ctx.lineTo(0, height);
      ctx.closePath();
      ctx.fillStyle = darkMode ? '#3a5a3a' : '#7CFC00';
      ctx.globalAlpha = 0.3;
      ctx.fill();
      ctx.globalAlpha = 1;
    };

    const drawClouds = () => {
      const width = canvas.width;

      cloudsRef.current.forEach((cloud) => {
        ctx.globalAlpha = cloud.opacity * (darkMode ? 0.3 : 0.8);
        ctx.fillStyle = darkMode ? '#4a4a6a' : '#ffffff';

        cloud.puffs.forEach((puff) => {
          ctx.beginPath();
          ctx.arc(
            cloud.x + puff.offsetX,
            cloud.y + puff.offsetY,
            puff.size,
            0,
            Math.PI * 2
          );
          ctx.fill();
        });

        // Move cloud
        cloud.x += cloud.speed;
        if (cloud.x > width + 100) {
          cloud.x = -150;
        }
      });
      ctx.globalAlpha = 1;
    };

    const drawLavender = (time: number) => {
      stemsRef.current.forEach((stem) => {
        const sway = Math.sin(time * 0.001 + stem.phase) * stem.swayAmount;

        // Draw stem
        ctx.beginPath();
        ctx.moveTo(stem.x, stem.baseY);

        const tipX = stem.x + sway * stem.height;
        const tipY = stem.baseY - stem.height;

        // Curved stem using quadratic bezier
        const cpX = stem.x + sway * stem.height * 0.5;
        const cpY = stem.baseY - stem.height * 0.5;

        ctx.quadraticCurveTo(cpX, cpY, tipX, tipY);
        ctx.strokeStyle = darkMode ? '#2d4a2d' : '#228B22';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Draw flowers along the stem
        stem.flowerOffsets.forEach((flower) => {
          const t = 1 - flower.y / (stem.height * 0.6);
          const fx = stem.x + sway * stem.height * t * t;
          const fy = stem.baseY - stem.height + flower.y;

          ctx.beginPath();
          ctx.arc(fx, fy, flower.size, 0, Math.PI * 2);
          ctx.fillStyle = darkMode
            ? `hsl(${flower.hue}, 40%, 35%)`
            : `hsl(${flower.hue}, 70%, 65%)`;
          ctx.fill();
        });
      });
    };

    const drawButterflies = (time: number) => {
      butterfliesRef.current.forEach((butterfly) => {
        // Handle resting state
        if (butterfly.isResting) {
          butterfly.restTimer -= 16;
          if (butterfly.restTimer <= 0) {
            butterfly.isResting = false;
            // Pick new target when leaving rest
            butterfly.targetX = Math.random() * canvas.width;
            butterfly.targetY = canvas.height * 0.3 + Math.random() * canvas.height * 0.35;
          }
          // Gentle wing pulse while resting
          butterfly.wingPhase += butterfly.wingSpeed * 0.3;
        } else {
          // Update position towards target with slower, curved flight
          const dx = butterfly.targetX - butterfly.x;
          const dy = butterfly.targetY - butterfly.y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < 30) {
            // Chance to rest near lavender
            if (butterfly.y > canvas.height * 0.45 && Math.random() < 0.02) {
              butterfly.isResting = true;
              butterfly.restTimer = 2000 + Math.random() * 3000;
              butterfly.vx = 0;
              butterfly.vy = 0;
            } else {
              // Pick new target
              butterfly.targetX = Math.random() * canvas.width;
              butterfly.targetY = canvas.height * 0.3 + Math.random() * canvas.height * 0.35;
            }
          }

          // Slower acceleration for more graceful movement
          butterfly.vx += dx * 0.00004;
          butterfly.vy += dy * 0.00004;

          // Add gentle curved path offset
          const curveOffset = Math.sin(time * 0.0008 + butterfly.flutterOffset) * 0.02;
          butterfly.vx += curveOffset;

          // Damping for smooth movement
          butterfly.vx *= 0.985;
          butterfly.vy *= 0.985;

          // Limit max speed
          const speed = Math.sqrt(butterfly.vx * butterfly.vx + butterfly.vy * butterfly.vy);
          if (speed > 0.8) {
            butterfly.vx = (butterfly.vx / speed) * 0.8;
            butterfly.vy = (butterfly.vy / speed) * 0.8;
          }

          butterfly.x += butterfly.vx;
          // Gentle bobbing motion
          butterfly.y += butterfly.vy + Math.sin(time * 0.001 + butterfly.flutterOffset) * 0.15;

          // Update body angle based on movement
          if (Math.abs(butterfly.vx) > 0.05) {
            const targetAngle = Math.atan2(butterfly.vy, butterfly.vx);
            butterfly.bodyAngle += (targetAngle - butterfly.bodyAngle) * 0.05;
          }

          // Normal wing flapping while flying
          butterfly.wingPhase += butterfly.wingSpeed;
        }

        // Keep in bounds
        butterfly.x = Math.max(20, Math.min(canvas.width - 20, butterfly.x));
        butterfly.y = Math.max(canvas.height * 0.2, Math.min(canvas.height * 0.7, butterfly.y));

        // Draw butterfly
        const size = butterfly.size;
        const wingCycle = butterfly.wingPhase;

        // Asymmetric wing motion - upstroke faster than downstroke
        const cyclePhase = wingCycle % (Math.PI * 2);
        let wingAngle: number;
        if (cyclePhase < Math.PI) {
          // Upstroke (faster)
          wingAngle = Math.sin(cyclePhase * 1.2) * 0.7;
        } else {
          // Downstroke (slower, more powerful)
          wingAngle = Math.sin(cyclePhase * 0.85 + 0.3) * 0.75;
        }

        // Reduce flapping when resting
        if (butterfly.isResting) {
          wingAngle *= 0.15;
        }

        ctx.save();
        ctx.translate(butterfly.x, butterfly.y);
        ctx.rotate(butterfly.bodyAngle * 0.3); // Subtle body tilt

        // Draw forewing (larger, more rounded) - Left
        ctx.save();
        ctx.rotate(-wingAngle * 1.1);
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.bezierCurveTo(
          -size * 0.4, -size * 0.6,
          -size * 1.0, -size * 0.5,
          -size * 0.9, size * 0.1
        );
        ctx.bezierCurveTo(
          -size * 0.6, size * 0.3,
          -size * 0.2, size * 0.2,
          0, 0
        );
        ctx.fillStyle = darkMode
          ? `hsl(${butterfly.hue}, 55%, 50%)`
          : `hsl(${butterfly.hue}, 85%, 60%)`;
        ctx.fill();
        // Wing pattern
        ctx.beginPath();
        ctx.arc(-size * 0.5, -size * 0.15, size * 0.15, 0, Math.PI * 2);
        ctx.fillStyle = darkMode
          ? `hsl(${butterfly.hue + 20}, 40%, 30%)`
          : `hsl(${butterfly.hue + 20}, 70%, 40%)`;
        ctx.fill();
        ctx.restore();

        // Draw forewing - Right
        ctx.save();
        ctx.rotate(wingAngle * 1.1);
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.bezierCurveTo(
          size * 0.4, -size * 0.6,
          size * 1.0, -size * 0.5,
          size * 0.9, size * 0.1
        );
        ctx.bezierCurveTo(
          size * 0.6, size * 0.3,
          size * 0.2, size * 0.2,
          0, 0
        );
        ctx.fillStyle = darkMode
          ? `hsl(${butterfly.hue}, 55%, 50%)`
          : `hsl(${butterfly.hue}, 85%, 60%)`;
        ctx.fill();
        // Wing pattern
        ctx.beginPath();
        ctx.arc(size * 0.5, -size * 0.15, size * 0.15, 0, Math.PI * 2);
        ctx.fillStyle = darkMode
          ? `hsl(${butterfly.hue + 20}, 40%, 30%)`
          : `hsl(${butterfly.hue + 20}, 70%, 40%)`;
        ctx.fill();
        ctx.restore();

        // Draw hindwing (smaller, rounder) - Left
        ctx.save();
        ctx.rotate(-wingAngle * 0.85);
        ctx.beginPath();
        ctx.moveTo(0, size * 0.1);
        ctx.bezierCurveTo(
          -size * 0.3, size * 0.1,
          -size * 0.7, size * 0.3,
          -size * 0.5, size * 0.6
        );
        ctx.bezierCurveTo(
          -size * 0.2, size * 0.5,
          0, size * 0.3,
          0, size * 0.1
        );
        ctx.fillStyle = darkMode
          ? `hsl(${butterfly.hue - 10}, 50%, 45%)`
          : `hsl(${butterfly.hue - 10}, 80%, 55%)`;
        ctx.fill();
        ctx.restore();

        // Draw hindwing - Right
        ctx.save();
        ctx.rotate(wingAngle * 0.85);
        ctx.beginPath();
        ctx.moveTo(0, size * 0.1);
        ctx.bezierCurveTo(
          size * 0.3, size * 0.1,
          size * 0.7, size * 0.3,
          size * 0.5, size * 0.6
        );
        ctx.bezierCurveTo(
          size * 0.2, size * 0.5,
          0, size * 0.3,
          0, size * 0.1
        );
        ctx.fillStyle = darkMode
          ? `hsl(${butterfly.hue - 10}, 50%, 45%)`
          : `hsl(${butterfly.hue - 10}, 80%, 55%)`;
        ctx.fill();
        ctx.restore();

        // Body (thorax + abdomen)
        ctx.fillStyle = darkMode ? '#2a2a2a' : '#333333';
        // Thorax
        ctx.beginPath();
        ctx.ellipse(0, 0, size * 0.1, size * 0.18, 0, 0, Math.PI * 2);
        ctx.fill();
        // Abdomen
        ctx.beginPath();
        ctx.ellipse(0, size * 0.25, size * 0.08, size * 0.2, 0, 0, Math.PI * 2);
        ctx.fill();
        // Head
        ctx.beginPath();
        ctx.arc(0, -size * 0.22, size * 0.08, 0, Math.PI * 2);
        ctx.fill();

        // Antennae
        ctx.strokeStyle = darkMode ? '#3a3a3a' : '#444444';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(-size * 0.02, -size * 0.25);
        ctx.quadraticCurveTo(-size * 0.15, -size * 0.4, -size * 0.12, -size * 0.5);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(size * 0.02, -size * 0.25);
        ctx.quadraticCurveTo(size * 0.15, -size * 0.4, size * 0.12, -size * 0.5);
        ctx.stroke();

        ctx.restore();
      });
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawSky();
      drawClouds();
      drawHills();
      drawLavender(time);
      drawButterflies(time);

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
