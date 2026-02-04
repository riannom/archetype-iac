/**
 * Zen Sand Garden Animation
 *
 * Peaceful raked sand patterns with moss-covered stones.
 * Occasional gentle ripples flow through the sand.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface Stone {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
  mossCoverage: number;
  mossPatches: { x: number; y: number; size: number }[];
  color: string;
}

interface SandRipple {
  centerX: number;
  centerY: number;
  radius: number;
  maxRadius: number;
  opacity: number;
  speed: number;
}

interface Leaf {
  x: number;
  y: number;
  rotation: number;
  size: number;
  color: string;
}

export function useZenSandGarden(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const stonesRef = useRef<Stone[]>([]);
  const ripplesRef = useRef<SandRipple[]>([]);
  const leavesRef = useRef<Leaf[]>([]);
  const rakePatternRef = useRef<{ x: number; curve: number }[]>([]);
  const animationRef = useRef<number>(0);
  const timeRef = useRef<number>(0);
  const nextRippleRef = useRef<number>(0);

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

      // Create stones with collision detection to prevent overlapping zones
      stonesRef.current = [];
      const stoneCount = 3 + Math.floor(Math.random() * 3);

      for (let i = 0; i < stoneCount; i++) {
        const stoneWidth = 40 + Math.random() * 60;
        const stoneHeight = 30 + Math.random() * 40;
        const zoneRadius = Math.max(stoneWidth, stoneHeight) * 3;

        // Try to find a position that doesn't overlap with existing stones' zones
        let x = 0, y = 0;
        let attempts = 0;
        const maxAttempts = 50;

        do {
          x = width * 0.15 + Math.random() * width * 0.7;
          y = height * 0.25 + Math.random() * height * 0.5;
          attempts++;

          // Check distance from all existing stones
          let tooClose = false;
          for (const existing of stonesRef.current) {
            const existingZoneRadius = Math.max(existing.width, existing.height) * 3;
            const minDist = zoneRadius + existingZoneRadius + 20; // Extra margin
            const dx = x - existing.x;
            const dy = y - existing.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < minDist) {
              tooClose = true;
              break;
            }
          }
          if (!tooClose) break;
        } while (attempts < maxAttempts);

        // Skip this stone if we couldn't find a valid position
        if (attempts >= maxAttempts && stonesRef.current.length > 0) {
          continue;
        }

        // Generate moss patches
        const mossPatches: { x: number; y: number; size: number }[] = [];
        const mossCoverage = 0.2 + Math.random() * 0.4;
        const patchCount = Math.floor(mossCoverage * 10);

        for (let m = 0; m < patchCount; m++) {
          mossPatches.push({
            x: (Math.random() - 0.5) * stoneWidth * 0.8,
            y: (Math.random() - 0.5) * stoneHeight * 0.6 - stoneHeight * 0.2,
            size: 5 + Math.random() * 10,
          });
        }

        const grayValue = 80 + Math.floor(Math.random() * 60);
        stonesRef.current.push({
          x,
          y,
          width: stoneWidth,
          height: stoneHeight,
          rotation: (Math.random() - 0.5) * 0.3,
          mossCoverage,
          mossPatches,
          color: `rgb(${grayValue}, ${grayValue - 5}, ${grayValue - 10})`,
        });
      }

      // Create rake pattern lines
      rakePatternRef.current = [];
      for (let x = 0; x < width + 50; x += 8) {
        rakePatternRef.current.push({
          x,
          curve: Math.sin(x * 0.02) * 10 + Math.sin(x * 0.005) * 20,
        });
      }

      // Create decorative leaves
      leavesRef.current = [];
      for (let i = 0; i < 5; i++) {
        leavesRef.current.push({
          x: Math.random() * width,
          y: Math.random() * height,
          rotation: Math.random() * Math.PI * 2,
          size: 8 + Math.random() * 8,
          color: Math.random() > 0.5 ? '#8B4513' : '#A0522D',
        });
      }

      // Initialize ripples array
      ripplesRef.current = [];
      nextRippleRef.current = 2000 + Math.random() * 3000;
    };

    // Helper to check if a point is within a stone's circular rake zone
    const isInStoneZone = (px: number, py: number): boolean => {
      for (const stone of stonesRef.current) {
        // Calculate the exclusion zone radius (where concentric circles are drawn)
        const maxRadius = Math.max(stone.width, stone.height) * 3;
        // Transform point to stone's coordinate system (accounting for rotation)
        const cos = Math.cos(-stone.rotation);
        const sin = Math.sin(-stone.rotation);
        const dx = px - stone.x;
        const dy = py - stone.y;
        const localX = dx * cos - dy * sin;
        const localY = dx * sin + dy * cos;
        // Check if inside the elliptical zone (with some margin for the inner stone)
        const normalizedDist = Math.sqrt(
          (localX * localX) / (maxRadius * maxRadius) +
          (localY * localY) / ((maxRadius * 0.7) * (maxRadius * 0.7))
        );
        if (normalizedDist < 1) {
          return true;
        }
      }
      return false;
    };

    const drawSand = (time: number) => {
      const width = canvas.width;
      const height = canvas.height;

      // Sand base
      const gradient = ctx.createLinearGradient(0, 0, 0, height);
      if (darkMode) {
        gradient.addColorStop(0, '#3a3a30');
        gradient.addColorStop(1, '#2a2a20');
      } else {
        gradient.addColorStop(0, '#E8DCC8');
        gradient.addColorStop(0.5, '#E0D4B8');
        gradient.addColorStop(1, '#D8CCB0');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, width, height);

      // First draw concentric circles around stones (so they're the "base" pattern in those areas)
      stonesRef.current.forEach((stone) => {
        ctx.strokeStyle = darkMode ? 'rgba(60, 60, 50, 0.35)' : 'rgba(180, 170, 150, 0.45)';
        ctx.lineWidth = 1;

        const maxRadius = Math.max(stone.width, stone.height) * 3;
        for (let r = stone.width * 0.8; r < maxRadius; r += 10) {
          ctx.beginPath();
          ctx.ellipse(
            stone.x,
            stone.y,
            r,
            r * 0.7,
            stone.rotation,
            0,
            Math.PI * 2
          );
          ctx.stroke();
        }
      });

      // Draw rake lines - but skip segments that would be inside stone zones
      ctx.strokeStyle = darkMode ? 'rgba(60, 60, 50, 0.4)' : 'rgba(180, 170, 150, 0.5)';
      ctx.lineWidth = 1;

      const waveOffset = Math.sin(time * 0.0002) * 5;

      for (let y = 20; y < height; y += 12) {
        let isDrawing = false;
        let lastInZone = false;

        rakePatternRef.current.forEach((point, index) => {
          const curveOffset = point.curve + Math.sin(y * 0.05 + point.x * 0.01) * 8 + waveOffset;
          const px = point.x;
          const py = y + curveOffset;

          const inZone = isInStoneZone(px, py);

          if (inZone) {
            // If we were drawing, end the current stroke
            if (isDrawing) {
              ctx.stroke();
              isDrawing = false;
            }
            lastInZone = true;
          } else {
            // Not in a stone zone
            if (lastInZone || index === 0) {
              // Just exited a zone or starting fresh - begin new path
              ctx.beginPath();
              ctx.moveTo(px, py);
              isDrawing = true;
            } else if (isDrawing) {
              // Continue the line
              ctx.lineTo(px, py);
            }
            lastInZone = false;
          }
        });

        // Finish any remaining stroke
        if (isDrawing) {
          ctx.stroke();
        }
      }
    };

    const drawRipples = () => {
      ripplesRef.current.forEach((ripple, index) => {
        ripple.radius += ripple.speed;
        ripple.opacity -= 0.003;

        if (ripple.opacity <= 0 || ripple.radius > ripple.maxRadius) {
          ripplesRef.current.splice(index, 1);
          return;
        }

        ctx.beginPath();
        ctx.arc(ripple.centerX, ripple.centerY, ripple.radius, 0, Math.PI * 2);
        ctx.strokeStyle = darkMode
          ? `rgba(80, 80, 70, ${ripple.opacity})`
          : `rgba(200, 190, 170, ${ripple.opacity})`;
        ctx.lineWidth = 2;
        ctx.stroke();
      });
    };

    const drawStones = () => {
      stonesRef.current.forEach((stone) => {
        ctx.save();
        ctx.translate(stone.x, stone.y);
        ctx.rotate(stone.rotation);

        // Stone shadow
        ctx.beginPath();
        ctx.ellipse(5, 5, stone.width * 0.5, stone.height * 0.35, 0, 0, Math.PI * 2);
        ctx.fillStyle = darkMode ? 'rgba(0, 0, 0, 0.3)' : 'rgba(0, 0, 0, 0.15)';
        ctx.fill();

        // Stone body
        ctx.beginPath();
        ctx.ellipse(0, 0, stone.width * 0.5, stone.height * 0.35, 0, 0, Math.PI * 2);

        const stoneGradient = ctx.createRadialGradient(
          -stone.width * 0.15,
          -stone.height * 0.1,
          0,
          0,
          0,
          stone.width * 0.5
        );
        if (darkMode) {
          stoneGradient.addColorStop(0, '#5a5a55');
          stoneGradient.addColorStop(1, '#3a3a35');
        } else {
          stoneGradient.addColorStop(0, '#9a9a95');
          stoneGradient.addColorStop(1, '#707065');
        }
        ctx.fillStyle = stoneGradient;
        ctx.fill();

        // Moss patches
        stone.mossPatches.forEach((moss) => {
          ctx.beginPath();
          ctx.arc(moss.x, moss.y, moss.size, 0, Math.PI * 2);
          ctx.fillStyle = darkMode
            ? `rgba(60, 90, 50, 0.7)`
            : `rgba(80, 120, 60, 0.6)`;
          ctx.fill();
        });

        ctx.restore();
      });
    };

    const drawLeaves = () => {
      leavesRef.current.forEach((leaf) => {
        ctx.save();
        ctx.translate(leaf.x, leaf.y);
        ctx.rotate(leaf.rotation);

        // Draw maple-like leaf
        ctx.fillStyle = darkMode ? '#5a3a2a' : leaf.color;
        ctx.beginPath();
        ctx.moveTo(0, -leaf.size);
        ctx.quadraticCurveTo(leaf.size * 0.5, -leaf.size * 0.5, leaf.size, 0);
        ctx.quadraticCurveTo(leaf.size * 0.5, leaf.size * 0.3, 0, leaf.size * 0.5);
        ctx.quadraticCurveTo(-leaf.size * 0.5, leaf.size * 0.3, -leaf.size, 0);
        ctx.quadraticCurveTo(-leaf.size * 0.5, -leaf.size * 0.5, 0, -leaf.size);
        ctx.fill();

        ctx.restore();
      });
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      // Occasionally create new ripple
      nextRippleRef.current -= 16;
      if (nextRippleRef.current <= 0 && ripplesRef.current.length < 3) {
        ripplesRef.current.push({
          centerX: canvas.width * 0.2 + Math.random() * canvas.width * 0.6,
          centerY: canvas.height * 0.2 + Math.random() * canvas.height * 0.6,
          radius: 0,
          maxRadius: 80 + Math.random() * 60,
          opacity: 0.5,
          speed: 0.3 + Math.random() * 0.2,
        });
        nextRippleRef.current = 3000 + Math.random() * 4000;
      }

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawSand(time);
      drawRipples();
      drawStones();
      drawLeaves();

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
