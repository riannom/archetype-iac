/**
 * Desert Dunes Animation
 *
 * Peaceful desert scene with rolling sand dunes, wind particles,
 * desert plants, and a warm color palette. Elements on sides.
 * All random values pre-generated for smooth, flicker-free animation.
 */

import { useEffect, RefObject } from 'react';

interface SandDune {
  x: number;
  baseY: number;
  width: number;
  height: number;
  layer: number; // 0 = back, 1 = mid, 2 = front
  colorOffset: number;
}

interface SandParticle {
  x: number;
  y: number;
  size: number;
  speed: number;
  opacity: number;
  wobble: number;
  wobbleSpeed: number;
}

interface DesertPlant {
  x: number;
  y: number;
  type: 'cactus' | 'succulent' | 'bush' | 'grass';
  size: number;
  swayPhase: number;
  // Pre-generated data for bush and grass
  clusters?: { x: number; y: number; size: number }[];
  grassHeights?: number[];
}

interface Tumbleweed {
  x: number;
  y: number;
  size: number;
  rotation: number;
  speed: number;
  bouncePhase: number;
  // Pre-generated branch data
  branches: { angle: number; length: number; midOffsetX: number; midOffsetY: number }[];
}

interface HeatWave {
  x: number;
  y: number;
  width: number;
  phase: number;
  speed: number;
}

interface Star {
  x: number;
  y: number;
  size: number;
  twinklePhase: number;
}

export function useDesertDunes(
  canvasRef: RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationId: number;
    let dunes: SandDune[] = [];
    let sandParticles: SandParticle[] = [];
    let plants: DesertPlant[] = [];
    let tumbleweeds: Tumbleweed[] = [];
    let heatWaves: HeatWave[] = [];
    let stars: Star[] = [];
    let timeRef = 0;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeScene();
    };

    const getRandomSidePosition = (width: number): number => {
      if (Math.random() < 0.7) {
        return Math.random() < 0.5
          ? Math.random() * width * 0.25
          : width * 0.75 + Math.random() * width * 0.25;
      }
      return Math.random() * width;
    };

    const generateBushClusters = (size: number): { x: number; y: number; size: number }[] => {
      const clusters = [];
      for (let i = 0; i < 5; i++) {
        clusters.push({
          x: (Math.random() - 0.5) * size * 0.6,
          y: -size * 0.3 - Math.random() * size * 0.4,
          size: size * 0.3 + Math.random() * size * 0.2,
        });
      }
      return clusters;
    };

    const generateGrassHeights = (size: number): number[] => {
      const heights = [];
      for (let i = 0; i < 8; i++) {
        heights.push(size * (0.6 + Math.random() * 0.4));
      }
      return heights;
    };

    const generateTumbleweedBranches = (size: number): Tumbleweed['branches'] => {
      const branches = [];
      for (let i = 0; i < 20; i++) {
        const angle = (i / 20) * Math.PI * 2;
        branches.push({
          angle,
          length: size * (0.7 + Math.random() * 0.3),
          midOffsetX: Math.random() * 5 - 2.5,
          midOffsetY: Math.random() * 5 - 2.5,
        });
      }
      return branches;
    };

    const initializeScene = () => {
      const { width, height } = canvas;

      // Create stars (pre-generated)
      stars = [];
      for (let i = 0; i < 50; i++) {
        stars.push({
          x: Math.random() * width,
          y: Math.random() * height * 0.4,
          size: Math.random() * 1.5,
          twinklePhase: Math.random() * Math.PI * 2,
        });
      }

      // Create layered dunes
      dunes = [];

      // Background dunes (layer 0)
      for (let i = 0; i < 4; i++) {
        dunes.push({
          x: (i / 4) * width - width * 0.1,
          baseY: height * 0.4,
          width: width * 0.5,
          height: 80 + Math.random() * 60,
          layer: 0,
          colorOffset: Math.random() * 20,
        });
      }

      // Middle dunes (layer 1)
      for (let i = 0; i < 5; i++) {
        dunes.push({
          x: (i / 5) * width - width * 0.15,
          baseY: height * 0.55,
          width: width * 0.45,
          height: 100 + Math.random() * 80,
          layer: 1,
          colorOffset: Math.random() * 20,
        });
      }

      // Foreground dunes (layer 2)
      for (let i = 0; i < 6; i++) {
        dunes.push({
          x: (i / 6) * width - width * 0.2,
          baseY: height * 0.75,
          width: width * 0.4,
          height: 120 + Math.random() * 100,
          layer: 2,
          colorOffset: Math.random() * 20,
        });
      }

      // Create sand particles
      sandParticles = [];
      const particleCount = Math.floor(width / 25);
      for (let i = 0; i < particleCount; i++) {
        sandParticles.push({
          x: Math.random() * width,
          y: Math.random() * height * 0.8,
          size: 1 + Math.random() * 1.5,
          speed: 0.5 + Math.random() * 1,
          opacity: 0.15 + Math.random() * 0.2,
          wobble: Math.random() * Math.PI * 2,
          wobbleSpeed: 0.02 + Math.random() * 0.02,
        });
      }

      // Create desert plants on sides with pre-generated data
      plants = [];
      const plantCount = Math.floor(width / 200);
      for (let i = 0; i < plantCount; i++) {
        const type = ['cactus', 'succulent', 'bush', 'grass'][Math.floor(Math.random() * 4)] as DesertPlant['type'];
        const size = 20 + Math.random() * 30;
        const plant: DesertPlant = {
          x: getRandomSidePosition(width),
          y: height * 0.6 + Math.random() * height * 0.35,
          type,
          size,
          swayPhase: Math.random() * Math.PI * 2,
        };

        // Pre-generate random data for types that need it
        if (type === 'bush') {
          plant.clusters = generateBushClusters(size);
        } else if (type === 'grass') {
          plant.grassHeights = generateGrassHeights(size);
        }

        plants.push(plant);
      }

      // Create tumbleweeds with pre-generated branches
      tumbleweeds = [];
      for (let i = 0; i < 2; i++) {
        const size = 15 + Math.random() * 15;
        tumbleweeds.push({
          x: -50 - Math.random() * 200,
          y: height * 0.7 + Math.random() * height * 0.2,
          size,
          rotation: 0,
          speed: 0.3 + Math.random() * 0.5,
          bouncePhase: Math.random() * Math.PI * 2,
          branches: generateTumbleweedBranches(size),
        });
      }

      // Create heat waves
      heatWaves = [];
      for (let i = 0; i < 5; i++) {
        heatWaves.push({
          x: Math.random() * width,
          y: height * 0.3 + Math.random() * height * 0.2,
          width: 100 + Math.random() * 150,
          phase: Math.random() * Math.PI * 2,
          speed: 0.008 + Math.random() * 0.008,
        });
      }
    };

    const drawSky = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
      const skyGradient = ctx.createLinearGradient(0, 0, 0, height * 0.6);
      if (darkMode) {
        // Night desert sky
        skyGradient.addColorStop(0, '#0a0a1a');
        skyGradient.addColorStop(0.3, '#1a1a3a');
        skyGradient.addColorStop(0.6, '#2a2040');
        skyGradient.addColorStop(1, '#3a2a50');
      } else {
        // Warm desert sky
        skyGradient.addColorStop(0, '#87CEEB');
        skyGradient.addColorStop(0.3, '#F0E68C');
        skyGradient.addColorStop(0.6, '#FFE4B5');
        skyGradient.addColorStop(1, '#FFDAB9');
      }
      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, width, height);

      // Sun/moon
      if (darkMode) {
        // Moon
        ctx.fillStyle = '#E8E8E8';
        ctx.beginPath();
        ctx.arc(width * 0.15, height * 0.15, 30, 0, Math.PI * 2);
        ctx.fill();

        // Moon craters
        ctx.fillStyle = 'rgba(200, 200, 200, 0.3)';
        ctx.beginPath();
        ctx.arc(width * 0.15 - 8, height * 0.15 - 5, 8, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(width * 0.15 + 10, height * 0.15 + 8, 5, 0, Math.PI * 2);
        ctx.fill();

        // Stars (using pre-generated positions)
        ctx.fillStyle = '#FFFFFF';
        stars.forEach(star => {
          const twinkle = Math.sin(timeRef * 0.003 + star.twinklePhase) * 0.5 + 0.5;
          ctx.globalAlpha = 0.3 + twinkle * 0.5;
          ctx.beginPath();
          ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
          ctx.fill();
        });
        ctx.globalAlpha = 1;
      } else {
        // Sun with glow
        const sunGlow = ctx.createRadialGradient(
          width * 0.85, height * 0.12, 0,
          width * 0.85, height * 0.12, 80
        );
        sunGlow.addColorStop(0, '#FFFACD');
        sunGlow.addColorStop(0.3, 'rgba(255, 250, 205, 0.5)');
        sunGlow.addColorStop(1, 'transparent');
        ctx.fillStyle = sunGlow;
        ctx.fillRect(width * 0.85 - 80, height * 0.12 - 80, 160, 160);

        ctx.fillStyle = '#FFD700';
        ctx.beginPath();
        ctx.arc(width * 0.85, height * 0.12, 35, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const drawDune = (ctx: CanvasRenderingContext2D, dune: SandDune, width: number, height: number) => {
      // Calculate colors based on layer
      let baseColor: string, shadowColor: string, highlightColor: string;

      if (darkMode) {
        const intensity = 60 + dune.layer * 20 + dune.colorOffset;
        baseColor = `rgb(${intensity}, ${intensity * 0.7}, ${intensity * 0.5})`;
        shadowColor = `rgb(${intensity * 0.6}, ${intensity * 0.4}, ${intensity * 0.3})`;
        highlightColor = `rgb(${intensity * 1.2}, ${intensity * 0.9}, ${intensity * 0.7})`;
      } else {
        const r = 210 + dune.layer * 15 + dune.colorOffset;
        const g = 180 + dune.layer * 10 + dune.colorOffset * 0.8;
        const b = 140 + dune.layer * 5 + dune.colorOffset * 0.5;
        baseColor = `rgb(${Math.min(255, r)}, ${Math.min(255, g)}, ${Math.min(255, b)})`;
        shadowColor = `rgb(${r * 0.85}, ${g * 0.8}, ${b * 0.75})`;
        highlightColor = `rgb(${Math.min(255, r * 1.1)}, ${Math.min(255, g * 1.05)}, ${Math.min(255, b)})`;
      }

      // Draw dune with curved shape
      const duneGradient = ctx.createLinearGradient(
        dune.x, dune.baseY - dune.height,
        dune.x + dune.width, dune.baseY
      );
      duneGradient.addColorStop(0, highlightColor);
      duneGradient.addColorStop(0.4, baseColor);
      duneGradient.addColorStop(1, shadowColor);

      ctx.fillStyle = duneGradient;
      ctx.beginPath();
      ctx.moveTo(dune.x, height);

      // Left slope
      ctx.lineTo(dune.x, dune.baseY);

      // Crest with curve
      const crestX = dune.x + dune.width * 0.7;
      const crestY = dune.baseY - dune.height;
      ctx.quadraticCurveTo(
        dune.x + dune.width * 0.3,
        dune.baseY - dune.height * 0.3,
        crestX,
        crestY
      );

      // Right slope (steeper)
      ctx.quadraticCurveTo(
        dune.x + dune.width * 0.9,
        dune.baseY - dune.height * 0.5,
        dune.x + dune.width,
        dune.baseY
      );

      ctx.lineTo(dune.x + dune.width, height);
      ctx.closePath();
      ctx.fill();

      // Add sand ripple texture on lit side (very slow movement)
      ctx.strokeStyle = highlightColor;
      ctx.lineWidth = 0.5;
      ctx.globalAlpha = 0.2;

      for (let i = 0; i < 5; i++) {
        const rippleY = dune.baseY - dune.height * 0.3 - i * 8;
        const ripplePhase = Math.sin(timeRef * 0.0003 + i) * 1.5;
        ctx.beginPath();
        ctx.moveTo(dune.x + dune.width * 0.2, rippleY + ripplePhase);
        ctx.quadraticCurveTo(
          dune.x + dune.width * 0.4,
          rippleY - 3 + ripplePhase,
          dune.x + dune.width * 0.6,
          rippleY + ripplePhase
        );
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    };

    const drawSandParticle = (ctx: CanvasRenderingContext2D, particle: SandParticle) => {
      const wobble = Math.sin(timeRef * particle.wobbleSpeed + particle.wobble) * 2;
      ctx.fillStyle = darkMode
        ? `rgba(180, 150, 120, ${particle.opacity})`
        : `rgba(220, 190, 150, ${particle.opacity})`;
      ctx.beginPath();
      ctx.arc(particle.x, particle.y + wobble, particle.size, 0, Math.PI * 2);
      ctx.fill();
    };

    const drawCactus = (ctx: CanvasRenderingContext2D, plant: DesertPlant) => {
      const sway = Math.sin(timeRef * 0.0008 + plant.swayPhase) * 1.5;
      const size = plant.size;

      ctx.save();
      ctx.translate(plant.x, plant.y);

      // Shadow
      ctx.fillStyle = 'rgba(0, 0, 0, 0.1)';
      ctx.beginPath();
      ctx.ellipse(5, 5, size * 0.4, size * 0.1, 0, 0, Math.PI * 2);
      ctx.fill();

      // Main body gradient
      const cactusGradient = ctx.createLinearGradient(-size * 0.3, 0, size * 0.3, 0);
      cactusGradient.addColorStop(0, darkMode ? '#1a4a2a' : '#2E8B57');
      cactusGradient.addColorStop(0.3, darkMode ? '#2a6a3a' : '#3CB371');
      cactusGradient.addColorStop(0.7, darkMode ? '#2a6a3a' : '#3CB371');
      cactusGradient.addColorStop(1, darkMode ? '#1a4a2a' : '#228B22');

      // Main body
      ctx.fillStyle = cactusGradient;
      ctx.beginPath();
      ctx.moveTo(-size * 0.15, 0);
      ctx.lineTo(-size * 0.15, -size * 1.5 + sway);
      ctx.quadraticCurveTo(0, -size * 1.7 + sway, size * 0.15, -size * 1.5 + sway);
      ctx.lineTo(size * 0.15, 0);
      ctx.closePath();
      ctx.fill();

      // Left arm
      ctx.beginPath();
      ctx.moveTo(-size * 0.15, -size * 0.6);
      ctx.lineTo(-size * 0.5, -size * 0.6 + sway * 0.5);
      ctx.lineTo(-size * 0.5, -size * 1.1 + sway * 0.5);
      ctx.quadraticCurveTo(-size * 0.35, -size * 1.25 + sway * 0.5, -size * 0.2, -size * 1.1 + sway * 0.5);
      ctx.lineTo(-size * 0.2, -size * 0.6);
      ctx.closePath();
      ctx.fill();

      // Right arm
      ctx.beginPath();
      ctx.moveTo(size * 0.15, -size * 0.9);
      ctx.lineTo(size * 0.45, -size * 0.9 + sway * 0.5);
      ctx.lineTo(size * 0.45, -size * 1.2 + sway * 0.5);
      ctx.quadraticCurveTo(size * 0.3, -size * 1.35 + sway * 0.5, size * 0.2, -size * 1.2 + sway * 0.5);
      ctx.lineTo(size * 0.2, -size * 0.9);
      ctx.closePath();
      ctx.fill();

      // Ridges
      ctx.strokeStyle = darkMode ? '#154a1a' : '#1a6a2a';
      ctx.lineWidth = 1;
      for (let i = 0; i < 4; i++) {
        const ridgeX = -size * 0.1 + (i / 3) * size * 0.2;
        ctx.beginPath();
        ctx.moveTo(ridgeX, 0);
        ctx.lineTo(ridgeX, -size * 1.4 + sway);
        ctx.stroke();
      }

      // Spines (small dots)
      ctx.fillStyle = darkMode ? '#888' : '#DDD';
      for (let y = 0; y < 8; y++) {
        for (let x = 0; x < 3; x++) {
          const spineX = -size * 0.1 + x * size * 0.1;
          const spineY = -size * 0.2 - y * size * 0.15;
          ctx.beginPath();
          ctx.arc(spineX, spineY + sway * (y / 8), 1, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      ctx.restore();
    };

    const drawSucculent = (ctx: CanvasRenderingContext2D, plant: DesertPlant) => {
      const size = plant.size;

      ctx.save();
      ctx.translate(plant.x, plant.y);

      // Draw rosette pattern
      const petalCount = 12;
      for (let ring = 2; ring >= 0; ring--) {
        const ringSize = size * (0.4 + ring * 0.3);
        const ringPetals = petalCount - ring * 2;

        for (let i = 0; i < ringPetals; i++) {
          const angle = (i / ringPetals) * Math.PI * 2 + ring * 0.2;
          const petalGradient = ctx.createRadialGradient(0, 0, 0, 0, 0, ringSize);
          petalGradient.addColorStop(0, darkMode ? '#4a7a5a' : '#7CB68A');
          petalGradient.addColorStop(0.5, darkMode ? '#3a6a4a' : '#66A87A');
          petalGradient.addColorStop(1, darkMode ? '#2a5a3a' : '#4A9A6A');

          ctx.fillStyle = petalGradient;
          ctx.save();
          ctx.rotate(angle);
          ctx.beginPath();
          ctx.ellipse(ringSize * 0.4, 0, ringSize * 0.5, ringSize * 0.2, 0, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        }
      }

      // Center
      ctx.fillStyle = darkMode ? '#5a9a6a' : '#90C9A0';
      ctx.beginPath();
      ctx.arc(0, 0, size * 0.15, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
    };

    const drawDesertBush = (ctx: CanvasRenderingContext2D, plant: DesertPlant) => {
      const sway = Math.sin(timeRef * 0.001 + plant.swayPhase) * 2;
      const size = plant.size;

      ctx.save();
      ctx.translate(plant.x, plant.y);

      // Shadow
      ctx.fillStyle = 'rgba(0, 0, 0, 0.1)';
      ctx.beginPath();
      ctx.ellipse(0, 5, size * 0.8, size * 0.2, 0, 0, Math.PI * 2);
      ctx.fill();

      // Bush clusters (using pre-generated data)
      const bushColor = darkMode ? '#4a5a3a' : '#8B9A6B';
      const bushHighlight = darkMode ? '#5a6a4a' : '#A0B080';

      if (plant.clusters) {
        plant.clusters.forEach(cluster => {
          const bushGradient = ctx.createRadialGradient(
            cluster.x - cluster.size * 0.2,
            cluster.y - cluster.size * 0.2,
            0,
            cluster.x, cluster.y, cluster.size
          );
          bushGradient.addColorStop(0, bushHighlight);
          bushGradient.addColorStop(1, bushColor);

          ctx.fillStyle = bushGradient;
          ctx.beginPath();
          ctx.arc(cluster.x + sway * 0.2, cluster.y + sway * 0.1, cluster.size, 0, Math.PI * 2);
          ctx.fill();
        });
      }

      ctx.restore();
    };

    const drawDesertGrass = (ctx: CanvasRenderingContext2D, plant: DesertPlant) => {
      const size = plant.size;

      ctx.save();
      ctx.translate(plant.x, plant.y);

      const grassColor = darkMode ? '#6a7a5a' : '#C9B896';

      if (plant.grassHeights) {
        plant.grassHeights.forEach((grassHeight, i) => {
          const sway = Math.sin(timeRef * 0.001 + plant.swayPhase + i * 0.5) * 3;
          const grassX = (i - 4) * 4;

          ctx.strokeStyle = grassColor;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(grassX, 0);
          ctx.quadraticCurveTo(
            grassX + sway * 0.5,
            -grassHeight * 0.5,
            grassX + sway,
            -grassHeight
          );
          ctx.stroke();
        });
      }

      ctx.restore();
    };

    const drawPlant = (ctx: CanvasRenderingContext2D, plant: DesertPlant) => {
      switch (plant.type) {
        case 'cactus':
          drawCactus(ctx, plant);
          break;
        case 'succulent':
          drawSucculent(ctx, plant);
          break;
        case 'bush':
          drawDesertBush(ctx, plant);
          break;
        case 'grass':
          drawDesertGrass(ctx, plant);
          break;
      }
    };

    const drawTumbleweed = (ctx: CanvasRenderingContext2D, tw: Tumbleweed) => {
      const bounce = Math.abs(Math.sin(timeRef * 0.008 + tw.bouncePhase)) * 4;

      ctx.save();
      ctx.translate(tw.x, tw.y - bounce);
      ctx.rotate(tw.rotation);

      // Tumbleweed structure (using pre-generated branches)
      ctx.strokeStyle = darkMode ? '#5a4a3a' : '#A08060';
      ctx.lineWidth = 1;

      tw.branches.forEach(branch => {
        ctx.beginPath();
        ctx.moveTo(0, 0);
        const midX = Math.cos(branch.angle) * branch.length * 0.5;
        const midY = Math.sin(branch.angle) * branch.length * 0.5;
        ctx.quadraticCurveTo(
          midX + branch.midOffsetX,
          midY + branch.midOffsetY,
          Math.cos(branch.angle) * branch.length,
          Math.sin(branch.angle) * branch.length
        );
        ctx.stroke();
      });

      ctx.restore();
    };

    const drawHeatWave = (ctx: CanvasRenderingContext2D, wave: HeatWave) => {
      if (darkMode) return; // No heat waves at night

      const waveY = wave.y + Math.sin(timeRef * wave.speed + wave.phase) * 2;

      ctx.globalAlpha = 0.02;
      ctx.fillStyle = '#FFFFFF';
      ctx.beginPath();
      for (let x = 0; x < wave.width; x += 5) {
        const y = waveY + Math.sin((x / 20) + timeRef * 0.003 + wave.phase) * 4;
        if (x === 0) {
          ctx.moveTo(wave.x + x, y);
        } else {
          ctx.lineTo(wave.x + x, y);
        }
      }
      ctx.lineTo(wave.x + wave.width, wave.y + 20);
      ctx.lineTo(wave.x, wave.y + 20);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 1;
    };

    const animate = () => {
      const { width, height } = canvas;
      timeRef++;

      // Clear canvas
      ctx.clearRect(0, 0, width, height);

      // Draw sky
      drawSky(ctx, width, height);

      // Draw heat waves (before dunes for layering)
      heatWaves.forEach(wave => drawHeatWave(ctx, wave));

      // Sort dunes by layer and draw
      dunes.sort((a, b) => a.layer - b.layer);
      dunes.forEach(dune => drawDune(ctx, dune, width, height));

      // Draw sand particles (slower movement)
      sandParticles.forEach(particle => {
        particle.x += particle.speed;
        if (particle.x > width + 10) {
          particle.x = -10;
          particle.y = Math.random() * height * 0.8;
        }
        drawSandParticle(ctx, particle);
      });

      // Draw plants
      plants.forEach(plant => drawPlant(ctx, plant));

      // Update and draw tumbleweeds (slower)
      tumbleweeds.forEach(tw => {
        tw.x += tw.speed;
        tw.rotation += tw.speed * 0.02;

        if (tw.x > width + 50) {
          tw.x = -50;
          tw.y = height * 0.7 + Math.random() * height * 0.2;
        }

        drawTumbleweed(ctx, tw);
      });

      animationId = requestAnimationFrame(animate);
    };

    resize();
    window.addEventListener('resize', resize);
    animate();

    return () => {
      window.removeEventListener('resize', resize);
      cancelAnimationFrame(animationId);
    };
  }, [canvasRef, darkMode, opacity, active]);
}
