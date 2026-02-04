/**
 * Volcanic Islands Animation
 *
 * Distant volcanic glow, palm tree silhouettes, calm ocean,
 * firefly-like lava particles rising into the night sky.
 */

import { useEffect, useRef } from 'react';

interface LavaParticle {
  x: number;
  y: number;
  size: number;
  speedY: number;
  speedX: number;
  brightness: number;
  life: number;
  maxLife: number;
}

interface PalmTree {
  x: number;
  y: number;
  height: number;
  trunkCurve: number;
  frondCount: number;
  swayPhase: number;
  swaySpeed: number;
}

interface OceanWave {
  y: number;
  phase: number;
  speed: number;
  amplitude: number;
}

interface Star {
  x: number;
  y: number;
  size: number;
  twinklePhase: number;
}

export function useVolcanicIslands(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  enabled: boolean
) {
  const particlesRef = useRef<LavaParticle[]>([]);
  const palmsRef = useRef<PalmTree[]>([]);
  const wavesRef = useRef<OceanWave[]>([]);
  const starsRef = useRef<Star[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef(0);
  const glowPulseRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;

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

    const width = canvas.width;
    const height = canvas.height;
    const horizonY = height * 0.55;
    const volcanoX = width * 0.85;
    const volcanoY = horizonY - height * 0.15;

    // Initialize stars
    starsRef.current = [];
    for (let i = 0; i < 60; i++) {
      starsRef.current.push({
        x: Math.random() * width,
        y: Math.random() * horizonY * 0.7,
        size: 0.5 + Math.random() * 1.2,
        twinklePhase: Math.random() * Math.PI * 2,
      });
    }

    // Initialize palm trees on the foreground island
    palmsRef.current = [];
    const islandLeft = width * 0.05;
    const islandRight = width * 0.35;
    for (let i = 0; i < 5; i++) {
      const x = islandLeft + Math.random() * (islandRight - islandLeft);
      palmsRef.current.push({
        x,
        y: horizonY + 10 + Math.random() * 20,
        height: 60 + Math.random() * 40,
        trunkCurve: (Math.random() - 0.5) * 30,
        frondCount: 6 + Math.floor(Math.random() * 3),
        swayPhase: Math.random() * Math.PI * 2,
        swaySpeed: 0.01 + Math.random() * 0.01,
      });
    }

    // Initialize ocean waves
    wavesRef.current = [];
    for (let i = 0; i < 4; i++) {
      wavesRef.current.push({
        y: horizonY + 20 + i * 25,
        phase: Math.random() * Math.PI * 2,
        speed: 0.01 + Math.random() * 0.01,
        amplitude: 3 + Math.random() * 4,
      });
    }

    particlesRef.current = [];

    const drawVolcano = (ctx: CanvasRenderingContext2D, x: number, baseY: number) => {
      const glowIntensity = 0.6 + Math.sin(glowPulseRef.current) * 0.3;

      // Sky glow behind volcano (draw first, behind everything)
      const skyGlow = ctx.createRadialGradient(
        x,
        baseY - 80,
        30,
        x,
        baseY - 80,
        250
      );
      skyGlow.addColorStop(0, `rgba(255, 80, 0, ${glowIntensity * 0.2})`);
      skyGlow.addColorStop(0.4, `rgba(200, 40, 0, ${glowIntensity * 0.1})`);
      skyGlow.addColorStop(1, 'rgba(100, 20, 0, 0)');

      ctx.beginPath();
      ctx.arc(x, baseY - 80, 250, 0, Math.PI * 2);
      ctx.fillStyle = skyGlow;
      ctx.fill();

      // Calculate offset to align volcano base with horizon
      const baseOffset = 160; // How far down the base extends from baseY

      // Volcano mountain silhouette - much larger and more dramatic
      ctx.beginPath();
      ctx.moveTo(x - 180, baseY + baseOffset);
      // Left slope with slight ridge
      ctx.lineTo(x - 120, baseY + 60);
      ctx.lineTo(x - 100, baseY + 70);
      ctx.lineTo(x - 50, baseY - 20);
      // Crater rim
      ctx.lineTo(x - 25, baseY - 40);
      ctx.lineTo(x - 15, baseY - 30); // Crater dip
      ctx.lineTo(x + 15, baseY - 30); // Crater dip
      ctx.lineTo(x + 25, baseY - 40);
      // Right slope
      ctx.lineTo(x + 50, baseY - 20);
      ctx.lineTo(x + 100, baseY + 70);
      ctx.lineTo(x + 120, baseY + 60);
      ctx.lineTo(x + 180, baseY + baseOffset);
      ctx.closePath();

      // Medium darkness for good contrast
      ctx.fillStyle = darkMode ? '#151010' : '#201510';
      ctx.fill();

      // Add subtle highlight on left slope for depth
      ctx.beginPath();
      ctx.moveTo(x - 180, baseY + baseOffset);
      ctx.lineTo(x - 120, baseY + 60);
      ctx.lineTo(x - 100, baseY + 70);
      ctx.lineTo(x - 50, baseY - 20);
      ctx.lineTo(x - 25, baseY - 40);
      ctx.lineTo(x - 180, baseY + baseOffset);
      ctx.closePath();
      ctx.fillStyle = darkMode ? 'rgba(50, 30, 25, 0.4)' : 'rgba(70, 40, 30, 0.3)';
      ctx.fill();

      // Volcanic glow at crater - larger
      const craterGlow = ctx.createRadialGradient(
        x,
        baseY - 25,
        8,
        x,
        baseY - 25,
        80
      );
      craterGlow.addColorStop(0, `rgba(255, 120, 20, ${glowIntensity * 0.9})`);
      craterGlow.addColorStop(0.2, `rgba(255, 80, 0, ${glowIntensity * 0.6})`);
      craterGlow.addColorStop(0.5, `rgba(255, 50, 0, ${glowIntensity * 0.3})`);
      craterGlow.addColorStop(0.7, `rgba(200, 30, 0, ${glowIntensity * 0.15})`);
      craterGlow.addColorStop(1, 'rgba(100, 0, 0, 0)');

      ctx.beginPath();
      ctx.arc(x, baseY - 25, 80, 0, Math.PI * 2);
      ctx.fillStyle = craterGlow;
      ctx.fill();

      // Lava glow inside crater
      const lavaGlow = ctx.createRadialGradient(
        x,
        baseY - 28,
        0,
        x,
        baseY - 28,
        20
      );
      lavaGlow.addColorStop(0, `rgba(255, 200, 100, ${glowIntensity})`);
      lavaGlow.addColorStop(0.5, `rgba(255, 150, 50, ${glowIntensity * 0.7})`);
      lavaGlow.addColorStop(1, `rgba(255, 100, 0, ${glowIntensity * 0.3})`);

      ctx.beginPath();
      ctx.arc(x, baseY - 28, 20, 0, Math.PI * 2);
      ctx.fillStyle = lavaGlow;
      ctx.fill();
    };

    const drawPalmTree = (ctx: CanvasRenderingContext2D, palm: PalmTree) => {
      const sway = Math.sin(palm.swayPhase) * 5;

      // Trunk
      ctx.beginPath();
      ctx.moveTo(palm.x, palm.y);
      const topX = palm.x + palm.trunkCurve + sway;
      const topY = palm.y - palm.height;

      // Curved trunk using quadratic bezier
      ctx.quadraticCurveTo(
        palm.x + palm.trunkCurve * 0.5,
        palm.y - palm.height * 0.5,
        topX,
        topY
      );

      ctx.strokeStyle = darkMode ? '#0a0505' : '#1a0f0a';
      ctx.lineWidth = 4;
      ctx.stroke();

      // Fronds
      const frondLength = 35 + Math.random() * 15;
      for (let i = 0; i < palm.frondCount; i++) {
        const angle = (i / palm.frondCount) * Math.PI - Math.PI / 2;
        const frondSway = Math.sin(palm.swayPhase + i * 0.3) * 0.1;

        ctx.beginPath();
        ctx.moveTo(topX, topY);

        const endX = topX + Math.cos(angle + frondSway) * frondLength;
        const endY = topY + Math.sin(angle + frondSway) * frondLength * 0.6 + 10;
        const ctrlX = topX + Math.cos(angle + frondSway) * frondLength * 0.6;
        const ctrlY = topY + Math.sin(angle + frondSway) * frondLength * 0.3;

        ctx.quadraticCurveTo(ctrlX, ctrlY, endX, endY);
        ctx.strokeStyle = darkMode ? '#080505' : '#151010';
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    };

    const animate = () => {
      const currentWidth = canvas.width;
      const currentHeight = canvas.height;
      ctx.clearRect(0, 0, currentWidth, currentHeight);
      timeRef.current += 0.016;
      glowPulseRef.current += 0.02;

      // Night sky gradient
      const skyGradient = ctx.createLinearGradient(0, 0, 0, horizonY);
      if (darkMode) {
        skyGradient.addColorStop(0, '#050510');
        skyGradient.addColorStop(0.4, '#0a0815');
        skyGradient.addColorStop(0.7, '#150a10');
        skyGradient.addColorStop(1, '#1a0a0a');
      } else {
        skyGradient.addColorStop(0, '#0a0a1a');
        skyGradient.addColorStop(0.4, '#150f20');
        skyGradient.addColorStop(0.7, '#201015');
        skyGradient.addColorStop(1, '#251510');
      }
      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, currentWidth, horizonY);

      // Draw stars
      starsRef.current.forEach((star) => {
        star.twinklePhase += 0.02;
        const twinkle = 0.4 + Math.sin(star.twinklePhase) * 0.4;

        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size * twinkle, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${twinkle})`;
        ctx.fill();
      });

      // Draw volcano (distant island)
      drawVolcano(ctx, volcanoX, volcanoY);

      // Ocean
      const oceanGradient = ctx.createLinearGradient(0, horizonY, 0, currentHeight);
      if (darkMode) {
        oceanGradient.addColorStop(0, '#0a0515');
        oceanGradient.addColorStop(0.3, '#080410');
        oceanGradient.addColorStop(1, '#05030a');
      } else {
        oceanGradient.addColorStop(0, '#100a1a');
        oceanGradient.addColorStop(0.3, '#0d0815');
        oceanGradient.addColorStop(1, '#0a0610');
      }
      ctx.fillStyle = oceanGradient;
      ctx.fillRect(0, horizonY, currentWidth, currentHeight - horizonY);

      // Volcanic glow reflection on water
      const waterGlowIntensity = 0.4 + Math.sin(glowPulseRef.current) * 0.2;
      const waterGlow = ctx.createRadialGradient(
        volcanoX,
        horizonY + 30,
        10,
        volcanoX,
        horizonY + 80,
        200
      );
      waterGlow.addColorStop(0, `rgba(255, 80, 0, ${waterGlowIntensity * 0.15})`);
      waterGlow.addColorStop(0.5, `rgba(200, 40, 0, ${waterGlowIntensity * 0.08})`);
      waterGlow.addColorStop(1, 'rgba(100, 20, 0, 0)');

      ctx.beginPath();
      ctx.ellipse(volcanoX, horizonY + 60, 200, 80, 0, 0, Math.PI * 2);
      ctx.fillStyle = waterGlow;
      ctx.fill();

      // Ocean waves
      wavesRef.current.forEach((wave) => {
        wave.phase += wave.speed;

        ctx.beginPath();
        ctx.moveTo(0, wave.y);
        for (let x = 0; x <= currentWidth; x += 10) {
          const y = wave.y + Math.sin(x * 0.01 + wave.phase) * wave.amplitude;
          ctx.lineTo(x, y);
        }
        ctx.strokeStyle = darkMode
          ? 'rgba(100, 100, 150, 0.15)'
          : 'rgba(120, 120, 170, 0.2)';
        ctx.lineWidth = 1;
        ctx.stroke();
      });

      // Foreground island silhouette
      ctx.beginPath();
      ctx.moveTo(0, horizonY + 30);
      ctx.quadraticCurveTo(
        currentWidth * 0.15,
        horizonY - 10,
        currentWidth * 0.35,
        horizonY + 40
      );
      ctx.lineTo(currentWidth * 0.35, currentHeight);
      ctx.lineTo(0, currentHeight);
      ctx.closePath();
      ctx.fillStyle = darkMode ? '#0a0505' : '#151010';
      ctx.fill();

      // Draw palm trees
      palmsRef.current.forEach((palm) => {
        palm.swayPhase += palm.swaySpeed;
        drawPalmTree(ctx, palm);
      });

      // Spawn lava particles from volcano
      if (Math.random() < 0.1) {
        particlesRef.current.push({
          x: volcanoX + (Math.random() - 0.5) * 20,
          y: volcanoY - 30,
          size: 1.5 + Math.random() * 2,
          speedY: -0.5 - Math.random() * 1,
          speedX: (Math.random() - 0.5) * 0.8,
          brightness: 0.7 + Math.random() * 0.3,
          life: 1,
          maxLife: 1,
        });
      }

      // Update and draw lava particles
      particlesRef.current = particlesRef.current.filter((p) => {
        p.x += p.speedX;
        p.y += p.speedY;
        p.speedY *= 0.995; // Slow down as they rise
        p.life -= 0.005;

        if (p.life <= 0) return false;

        const alpha = p.life * p.brightness;

        // Glow
        const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 4);
        glow.addColorStop(0, `rgba(255, 150, 50, ${alpha * 0.6})`);
        glow.addColorStop(0.5, `rgba(255, 80, 0, ${alpha * 0.3})`);
        glow.addColorStop(1, 'rgba(200, 50, 0, 0)');

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size * 4, 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();

        // Core
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 200, 100, ${alpha})`;
        ctx.fill();

        return true;
      });

      // Limit particles
      if (particlesRef.current.length > 100) {
        particlesRef.current = particlesRef.current.slice(-80);
      }

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [canvasRef, darkMode, opacity, enabled]);
}
