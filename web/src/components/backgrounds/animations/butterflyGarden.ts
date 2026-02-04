/**
 * Butterfly Garden Animation (replaces triangles)
 *
 * Colorful butterflies fluttering among flowers in a meadow.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface Butterfly {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  wingPhase: number;
  wingSpeed: number;
  hue: number;
  pattern: number;
  targetX: number;
  targetY: number;
  restTimer: number;
  bodyAngle: number;
  flutterOffset: number;
  // Additional properties for more random flight
  wanderPhase: number;
  wanderSpeed: number;
  zigzagPhase: number;
  directionChangeTimer: number;
}

interface Flower {
  x: number;
  y: number;
  size: number;
  petalCount: number;
  hue: number;
  swayPhase: number;
  stemHeight: number;
}

export function useButterflyGarden(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const butterfliesRef = useRef<Butterfly[]>([]);
  const flowersRef = useRef<Flower[]>([]);
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

      // Create flowers
      flowersRef.current = [];
      const flowerCount = Math.floor(width / 60);
      for (let i = 0; i < flowerCount; i++) {
        flowersRef.current.push({
          x: (i / flowerCount) * width + (Math.random() - 0.5) * 50,
          y: height * 0.6 + Math.random() * height * 0.35,
          size: 15 + Math.random() * 20,
          petalCount: 5 + Math.floor(Math.random() * 3),
          hue: [320, 40, 280, 200, 350][Math.floor(Math.random() * 5)],
          swayPhase: Math.random() * Math.PI * 2,
          stemHeight: 40 + Math.random() * 60,
        });
      }

      // Create butterflies
      butterfliesRef.current = [];
      for (let i = 0; i < 8; i++) {
        butterfliesRef.current.push({
          x: Math.random() * width,
          y: height * 0.2 + Math.random() * height * 0.5,
          vx: 0,
          vy: 0,
          size: 12 + Math.random() * 10,
          wingPhase: Math.random() * Math.PI * 2,
          wingSpeed: 0.10 + Math.random() * 0.05, // Slower wing flapping
          hue: [280, 30, 180, 320, 50][Math.floor(Math.random() * 5)],
          pattern: Math.floor(Math.random() * 3),
          targetX: Math.random() * width,
          targetY: height * 0.2 + Math.random() * height * 0.5,
          restTimer: 0,
          bodyAngle: 0,
          flutterOffset: Math.random() * Math.PI * 2,
          // Random wandering properties for more erratic flight
          wanderPhase: Math.random() * Math.PI * 2,
          wanderSpeed: 0.002 + Math.random() * 0.003,
          zigzagPhase: Math.random() * Math.PI * 2,
          directionChangeTimer: 30 + Math.random() * 60,
        });
      }
    };

    const drawBackground = () => {
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
      if (darkMode) {
        gradient.addColorStop(0, '#1a1a2e');
        gradient.addColorStop(0.6, '#2a2a3e');
        gradient.addColorStop(1, '#1a2a1a');
      } else {
        gradient.addColorStop(0, '#87CEEB');
        gradient.addColorStop(0.6, '#98D8C8');
        gradient.addColorStop(1, '#7CB342');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    };

    const drawFlower = (flower: Flower, time: number) => {
      const sway = Math.sin(time * 0.001 + flower.swayPhase) * 3;

      ctx.save();
      ctx.translate(flower.x + sway, flower.y);

      // Stem
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.quadraticCurveTo(sway * 2, -flower.stemHeight / 2, sway, -flower.stemHeight);
      ctx.strokeStyle = darkMode ? '#2a4a2a' : '#4CAF50';
      ctx.lineWidth = 3;
      ctx.stroke();

      // Flower head
      ctx.translate(sway, -flower.stemHeight);

      // Petals
      for (let p = 0; p < flower.petalCount; p++) {
        const angle = (p / flower.petalCount) * Math.PI * 2;
        ctx.save();
        ctx.rotate(angle);
        ctx.beginPath();
        ctx.ellipse(0, -flower.size * 0.6, flower.size * 0.35, flower.size * 0.6, 0, 0, Math.PI * 2);
        ctx.fillStyle = darkMode
          ? `hsla(${flower.hue}, 40%, 40%, 0.8)`
          : `hsla(${flower.hue}, 70%, 70%, 0.9)`;
        ctx.fill();
        ctx.restore();
      }

      // Center
      ctx.beginPath();
      ctx.arc(0, 0, flower.size * 0.25, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? '#aa8800' : '#FFD700';
      ctx.fill();

      ctx.restore();
    };

    const drawButterfly = (butterfly: Butterfly, time: number) => {
      // Handle resting state
      if (butterfly.restTimer > 0) {
        butterfly.restTimer--;
        butterfly.wingPhase += butterfly.wingSpeed * 0.12; // Slow wing movement when resting
      } else {
        // Update wandering phases
        butterfly.wanderPhase += butterfly.wanderSpeed;
        butterfly.zigzagPhase += 0.015 + Math.random() * 0.01;

        // Direction change timer for more erratic movement
        butterfly.directionChangeTimer--;
        if (butterfly.directionChangeTimer <= 0) {
          butterfly.directionChangeTimer = 40 + Math.random() * 80;
          // Random direction shift
          butterfly.targetX += (Math.random() - 0.5) * 150;
          butterfly.targetY += (Math.random() - 0.5) * 100;
          // Keep in bounds
          butterfly.targetX = Math.max(50, Math.min(canvas.width - 50, butterfly.targetX));
          butterfly.targetY = Math.max(canvas.height * 0.1, Math.min(canvas.height * 0.7, butterfly.targetY));
        }

        // Update movement with more organic, erratic flight
        const dx = butterfly.targetX - butterfly.x;
        const dy = butterfly.targetY - butterfly.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < 30) {
          // Pick new target, sometimes near a flower
          if (Math.random() < 0.5 && flowersRef.current.length > 0) {
            const flower = flowersRef.current[Math.floor(Math.random() * flowersRef.current.length)];
            butterfly.targetX = flower.x + (Math.random() - 0.5) * 60;
            butterfly.targetY = flower.y - flower.stemHeight - 20 + Math.random() * 40;
            butterfly.restTimer = 80 + Math.random() * 160; // Rest on flower longer
          } else {
            butterfly.targetX = Math.random() * canvas.width;
            butterfly.targetY = canvas.height * 0.15 + Math.random() * canvas.height * 0.5;
          }
        }

        // Much slower, more curved flight path - reduced acceleration
        const accel = 0.0003; // Much slower acceleration
        butterfly.vx += dx * accel;
        butterfly.vy += dy * accel;

        // Add complex oscillation for erratic, random-looking flight
        // Multiple overlapping sine waves create unpredictable motion
        const wanderX = Math.sin(butterfly.wanderPhase) * 0.04
          + Math.sin(butterfly.wanderPhase * 2.3 + 1.5) * 0.025
          + Math.sin(butterfly.zigzagPhase * 1.7) * 0.03;
        const wanderY = Math.cos(butterfly.wanderPhase * 0.8) * 0.035
          + Math.sin(butterfly.zigzagPhase + 0.8) * 0.025
          + Math.cos(butterfly.wanderPhase * 1.4 + 2.1) * 0.02;

        butterfly.vx += wanderX;
        butterfly.vy += wanderY;

        // Stronger damping for slower overall movement
        butterfly.vx *= 0.93;
        butterfly.vy *= 0.93;

        // Clamp maximum velocity
        const maxSpeed = 0.8;
        const speed = Math.sqrt(butterfly.vx * butterfly.vx + butterfly.vy * butterfly.vy);
        if (speed > maxSpeed) {
          butterfly.vx = (butterfly.vx / speed) * maxSpeed;
          butterfly.vy = (butterfly.vy / speed) * maxSpeed;
        }

        // Apply velocity with gentle bobbing
        butterfly.x += butterfly.vx;
        butterfly.y += butterfly.vy + Math.sin(time * 0.002 + butterfly.wingPhase) * 0.25;

        // Smooth body angle based on movement direction
        if (speed > 0.1) {
          const targetAngle = Math.atan2(butterfly.vy, butterfly.vx);
          butterfly.bodyAngle += (targetAngle - butterfly.bodyAngle) * 0.06;
        }

        butterfly.wingPhase += butterfly.wingSpeed;
      }

      ctx.save();
      ctx.translate(butterfly.x, butterfly.y);

      // Subtle body rotation based on flight direction
      const bodyTilt = butterfly.restTimer > 0 ? 0 : Math.sin(butterfly.bodyAngle) * 0.15;
      ctx.rotate(bodyTilt);

      // Wing flap - ROTATION around attachment point (like a hinge)
      // This is the key: wings pivot at the body, tip swings up/down
      const flapAngle = Math.sin(butterfly.wingPhase) * 0.9; // ~50 degrees each way
      const hindwingFlapAngle = Math.sin(butterfly.wingPhase - 0.25) * 0.85; // Slight delay for hindwings

      const size = butterfly.size;

      // Wing colors
      const wingColor = darkMode
        ? `hsl(${butterfly.hue}, 55%, 48%)`
        : `hsl(${butterfly.hue}, 75%, 62%)`;
      const wingColorDark = darkMode
        ? `hsl(${butterfly.hue}, 45%, 35%)`
        : `hsl(${butterfly.hue}, 65%, 45%)`;
      const wingUnderside = darkMode
        ? `hsl(${butterfly.hue}, 35%, 38%)`
        : `hsl(${butterfly.hue}, 55%, 52%)`;

      // Foreshortening: wings appear thinner when angled up/down
      // cos(angle) gives 1.0 when flat, smaller when angled
      const foreshorten = 0.5 + 0.5 * Math.cos(flapAngle * 1.2);
      const hindwingForeshorten = 0.5 + 0.5 * Math.cos(hindwingFlapAngle * 1.2);

      // Show underside when wings are angled upward
      const showUnderside = flapAngle > 0.15;

      // Left forewing - ROTATE around attachment point
      ctx.save();
      ctx.translate(-size * 0.08, 0);
      ctx.rotate(flapAngle); // Positive = wing tip goes UP (for left wing extending to -X)
      ctx.scale(1, foreshorten); // Foreshortening effect
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.bezierCurveTo(
        -size * 0.35, -size * 0.25,
        -size * 0.85, -size * 0.15,
        -size * 0.8, size * 0.1
      );
      ctx.bezierCurveTo(
        -size * 0.5, size * 0.2,
        -size * 0.15, size * 0.08,
        0, 0
      );
      ctx.fillStyle = showUnderside ? wingUnderside : wingColor;
      ctx.fill();
      ctx.strokeStyle = wingColorDark;
      ctx.lineWidth = 1;
      ctx.stroke();
      // Wing pattern (only visible on topside)
      if (!showUnderside) {
        if (butterfly.pattern === 0) {
          ctx.beginPath();
          ctx.arc(-size * 0.4, -size * 0.02, size * 0.12, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(255,255,255,0.5)';
          ctx.fill();
          ctx.beginPath();
          ctx.arc(-size * 0.58, size * 0.06, size * 0.08, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(0,0,0,0.25)';
          ctx.fill();
        } else if (butterfly.pattern === 1) {
          ctx.beginPath();
          ctx.arc(-size * 0.45, 0, size * 0.1, 0, Math.PI * 2);
          ctx.fillStyle = `hsla(${(butterfly.hue + 180) % 360}, 60%, 50%, 0.5)`;
          ctx.fill();
        }
      }
      ctx.restore();

      // Left hindwing - ROTATE around attachment point
      ctx.save();
      ctx.translate(-size * 0.08, size * 0.1);
      ctx.rotate(hindwingFlapAngle); // Same direction as left forewing
      ctx.scale(1, hindwingForeshorten);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.bezierCurveTo(
        -size * 0.3, size * 0.05,
        -size * 0.5, size * 0.25,
        -size * 0.35, size * 0.4
      );
      ctx.bezierCurveTo(
        -size * 0.15, size * 0.35,
        -size * 0.05, size * 0.18,
        0, 0
      );
      ctx.fillStyle = showUnderside ? wingUnderside : wingColor;
      ctx.fill();
      ctx.strokeStyle = wingColorDark;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();

      // Right forewing - ROTATE opposite direction (mirror of left)
      ctx.save();
      ctx.translate(size * 0.08, 0);
      ctx.rotate(-flapAngle); // NEGATIVE = wing tip goes UP (for right wing extending to +X)
      ctx.scale(1, foreshorten);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.bezierCurveTo(
        size * 0.35, -size * 0.25,
        size * 0.85, -size * 0.15,
        size * 0.8, size * 0.1
      );
      ctx.bezierCurveTo(
        size * 0.5, size * 0.2,
        size * 0.15, size * 0.08,
        0, 0
      );
      ctx.fillStyle = showUnderside ? wingUnderside : wingColor;
      ctx.fill();
      ctx.strokeStyle = wingColorDark;
      ctx.lineWidth = 1;
      ctx.stroke();
      if (!showUnderside) {
        if (butterfly.pattern === 0) {
          ctx.beginPath();
          ctx.arc(size * 0.4, -size * 0.02, size * 0.12, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(255,255,255,0.5)';
          ctx.fill();
          ctx.beginPath();
          ctx.arc(size * 0.58, size * 0.06, size * 0.08, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(0,0,0,0.25)';
          ctx.fill();
        } else if (butterfly.pattern === 1) {
          ctx.beginPath();
          ctx.arc(size * 0.45, 0, size * 0.1, 0, Math.PI * 2);
          ctx.fillStyle = `hsla(${(butterfly.hue + 180) % 360}, 60%, 50%, 0.5)`;
          ctx.fill();
        }
      }
      ctx.restore();

      // Right hindwing - ROTATE opposite direction (mirror of left)
      ctx.save();
      ctx.translate(size * 0.08, size * 0.1);
      ctx.rotate(-hindwingFlapAngle); // NEGATIVE to match right forewing
      ctx.scale(1, hindwingForeshorten);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.bezierCurveTo(
        size * 0.3, size * 0.05,
        size * 0.5, size * 0.25,
        size * 0.35, size * 0.4
      );
      ctx.bezierCurveTo(
        size * 0.15, size * 0.35,
        size * 0.05, size * 0.18,
        0, 0
      );
      ctx.fillStyle = showUnderside ? wingUnderside : wingColor;
      ctx.fill();
      ctx.strokeStyle = wingColorDark;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();

      // Body
      ctx.beginPath();
      ctx.ellipse(0, size * 0.15, size * 0.08, size * 0.35, 0, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? '#2a2a2a' : '#333';
      ctx.fill();

      // Thorax
      ctx.beginPath();
      ctx.ellipse(0, 0, size * 0.1, size * 0.12, 0, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? '#3a3a3a' : '#444';
      ctx.fill();

      // Head
      ctx.beginPath();
      ctx.arc(0, -size * 0.18, size * 0.08, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? '#2a2a2a' : '#333';
      ctx.fill();

      // Antennae with curl
      const antennaWave = Math.sin(time * 0.008 + butterfly.flutterOffset) * 0.1;
      ctx.beginPath();
      ctx.moveTo(-size * 0.03, -size * 0.25);
      ctx.bezierCurveTo(
        -size * 0.15, -size * 0.45,
        -size * 0.2 + antennaWave * size, -size * 0.55,
        -size * 0.12, -size * 0.6
      );
      ctx.moveTo(size * 0.03, -size * 0.25);
      ctx.bezierCurveTo(
        size * 0.15, -size * 0.45,
        size * 0.2 - antennaWave * size, -size * 0.55,
        size * 0.12, -size * 0.6
      );
      ctx.strokeStyle = darkMode ? '#3a3a3a' : '#444';
      ctx.lineWidth = 1.2;
      ctx.stroke();

      // Antenna tips
      ctx.beginPath();
      ctx.arc(-size * 0.12, -size * 0.6, size * 0.025, 0, Math.PI * 2);
      ctx.arc(size * 0.12, -size * 0.6, size * 0.025, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? '#3a3a3a' : '#444';
      ctx.fill();

      ctx.restore();
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawBackground();

      // Draw flowers (sorted by y for depth)
      const sortedFlowers = [...flowersRef.current].sort((a, b) => a.y - b.y);
      sortedFlowers.forEach((flower) => drawFlower(flower, time));

      // Draw butterflies
      butterfliesRef.current.forEach((butterfly) => drawButterfly(butterfly, time));

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
