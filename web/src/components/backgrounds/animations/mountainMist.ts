/**
 * Mountain Mist Animation
 *
 * Serene mountain landscape with layered peaks, drifting mist,
 * pine trees, and a peaceful atmosphere. Elements on sides.
 */

import { useEffect, RefObject } from 'react';

interface Mountain {
  x: number;
  baseY: number;
  width: number;
  height: number;
  layer: number;
  snowCap: boolean;
  peakOffset: number;
}

interface MistLayer {
  y: number;
  density: number;
  speed: number;
  offset: number;
  height: number;
}

interface Tree {
  x: number;
  y: number;
  height: number;
  type: 'pine' | 'fir' | 'bare';
  layer: number;
}

interface Cloud {
  x: number;
  y: number;
  size: number;
  speed: number;
  puffs: { x: number; y: number; size: number }[];
}

interface Bird {
  x: number;
  y: number;
  wingPhase: number;
  speed: number;
  size: number;
  direction: 1 | -1;
}

interface WaterReflection {
  y: number;
  ripplePhase: number;
}

export function useMountainMist(
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
    let mountains: Mountain[] = [];
    let mistLayers: MistLayer[] = [];
    let trees: Tree[] = [];
    let clouds: Cloud[] = [];
    let birds: Bird[] = [];
    let waterReflection: WaterReflection | null = null;
    let timeRef = 0;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeScene();
    };

    const getRandomSidePosition = (width: number): number => {
      if (Math.random() < 0.7) {
        return Math.random() < 0.5
          ? Math.random() * width * 0.3
          : width * 0.7 + Math.random() * width * 0.3;
      }
      return Math.random() * width;
    };

    const initializeScene = () => {
      const { width, height } = canvas;

      // Create layered mountains
      mountains = [];

      // Background mountains (layer 0) - tallest, most distant
      for (let i = 0; i < 3; i++) {
        mountains.push({
          x: width * 0.15 + i * width * 0.3,
          baseY: height * 0.5,
          width: width * 0.5,
          height: height * 0.35 + Math.random() * height * 0.1,
          layer: 0,
          snowCap: true,
          peakOffset: (Math.random() - 0.5) * width * 0.1,
        });
      }

      // Middle mountains (layer 1)
      for (let i = 0; i < 4; i++) {
        mountains.push({
          x: width * 0.05 + i * width * 0.28,
          baseY: height * 0.6,
          width: width * 0.4,
          height: height * 0.25 + Math.random() * height * 0.08,
          layer: 1,
          snowCap: Math.random() > 0.5,
          peakOffset: (Math.random() - 0.5) * width * 0.08,
        });
      }

      // Foreground mountains (layer 2) - closest
      for (let i = 0; i < 5; i++) {
        mountains.push({
          x: width * -0.1 + i * width * 0.3,
          baseY: height * 0.75,
          width: width * 0.35,
          height: height * 0.15 + Math.random() * height * 0.05,
          layer: 2,
          snowCap: false,
          peakOffset: (Math.random() - 0.5) * width * 0.05,
        });
      }

      // Create mist layers
      mistLayers = [];
      for (let i = 0; i < 5; i++) {
        mistLayers.push({
          y: height * 0.4 + i * height * 0.1,
          density: 0.1 + Math.random() * 0.15,
          speed: 0.1 + Math.random() * 0.2,
          offset: Math.random() * 1000,
          height: 40 + Math.random() * 60,
        });
      }

      // Create trees on sides
      trees = [];
      const treeCount = Math.floor(width / 40);
      for (let i = 0; i < treeCount; i++) {
        const x = getRandomSidePosition(width);
        const layer = Math.floor(Math.random() * 3);
        const baseY = height * (0.55 + layer * 0.1);

        trees.push({
          x,
          y: baseY + Math.random() * height * 0.15,
          height: 30 + Math.random() * 50 * (3 - layer) / 3,
          type: ['pine', 'fir', 'bare'][Math.floor(Math.random() * 3)] as Tree['type'],
          layer,
        });
      }

      // Sort trees by layer for proper depth
      trees.sort((a, b) => a.layer - b.layer);

      // Create clouds
      clouds = [];
      for (let i = 0; i < 4; i++) {
        const puffs = [];
        const puffCount = 3 + Math.floor(Math.random() * 3);
        for (let p = 0; p < puffCount; p++) {
          puffs.push({
            x: (p - puffCount / 2) * 30,
            y: (Math.random() - 0.5) * 15,
            size: 25 + Math.random() * 25,
          });
        }
        clouds.push({
          x: i * width / 3 + Math.random() * 100,
          y: height * 0.08 + Math.random() * height * 0.12,
          size: 1,
          speed: 0.1 + Math.random() * 0.15,
          puffs,
        });
      }

      // Create birds
      birds = [];
      for (let i = 0; i < 5; i++) {
        const direction = Math.random() < 0.5 ? 1 : -1;
        birds.push({
          x: direction === 1 ? -20 - Math.random() * 100 : width + 20 + Math.random() * 100,
          y: height * 0.1 + Math.random() * height * 0.25,
          wingPhase: Math.random() * Math.PI * 2,
          speed: 0.5 + Math.random() * 0.8,
          size: 3 + Math.random() * 3,
          direction,
        });
      }

      // Water reflection at bottom
      waterReflection = {
        y: height * 0.85,
        ripplePhase: 0,
      };
    };

    const drawSky = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
      const skyGradient = ctx.createLinearGradient(0, 0, 0, height * 0.6);
      if (darkMode) {
        skyGradient.addColorStop(0, '#1a2a3a');
        skyGradient.addColorStop(0.3, '#2a3a4a');
        skyGradient.addColorStop(0.6, '#3a4a5a');
        skyGradient.addColorStop(1, '#4a5a6a');
      } else {
        skyGradient.addColorStop(0, '#87CEEB');
        skyGradient.addColorStop(0.3, '#B0D4E8');
        skyGradient.addColorStop(0.6, '#D4E5F0');
        skyGradient.addColorStop(1, '#E8F0F5');
      }
      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, width, height);

      // Sun/moon glow
      const glowX = darkMode ? width * 0.15 : width * 0.8;
      const glowY = height * 0.12;
      const glowGradient = ctx.createRadialGradient(glowX, glowY, 0, glowX, glowY, 100);

      if (darkMode) {
        glowGradient.addColorStop(0, 'rgba(200, 210, 230, 0.5)');
        glowGradient.addColorStop(0.5, 'rgba(200, 210, 230, 0.2)');
        glowGradient.addColorStop(1, 'transparent');
      } else {
        glowGradient.addColorStop(0, 'rgba(255, 250, 220, 0.8)');
        glowGradient.addColorStop(0.3, 'rgba(255, 250, 200, 0.4)');
        glowGradient.addColorStop(1, 'transparent');
      }
      ctx.fillStyle = glowGradient;
      ctx.beginPath();
      ctx.arc(glowX, glowY, 100, 0, Math.PI * 2);
      ctx.fill();

      // Sun/moon
      ctx.fillStyle = darkMode ? '#E0E8F0' : '#FFF8DC';
      ctx.beginPath();
      ctx.arc(glowX, glowY, 25, 0, Math.PI * 2);
      ctx.fill();
    };

    const drawCloud = (ctx: CanvasRenderingContext2D, cloud: Cloud) => {
      ctx.fillStyle = darkMode ? 'rgba(80, 100, 120, 0.4)' : 'rgba(255, 255, 255, 0.8)';

      cloud.puffs.forEach(puff => {
        ctx.beginPath();
        ctx.arc(cloud.x + puff.x, cloud.y + puff.y, puff.size, 0, Math.PI * 2);
        ctx.fill();
      });
    };

    const drawMountain = (ctx: CanvasRenderingContext2D, mountain: Mountain, height: number) => {
      const peakX = mountain.x + mountain.width / 2 + mountain.peakOffset;
      const peakY = mountain.baseY - mountain.height;

      // Calculate colors based on layer (atmospheric perspective)
      let baseColor: string, shadowColor: string, highlightColor: string;

      if (darkMode) {
        const intensity = 30 + mountain.layer * 20;
        baseColor = `rgb(${intensity + 20}, ${intensity + 30}, ${intensity + 40})`;
        shadowColor = `rgb(${intensity}, ${intensity + 10}, ${intensity + 20})`;
        highlightColor = `rgb(${intensity + 40}, ${intensity + 50}, ${intensity + 60})`;
      } else {
        const blueShift = 180 - mountain.layer * 40;
        const greenShift = 200 - mountain.layer * 35;
        const redShift = 190 - mountain.layer * 30;
        baseColor = `rgb(${redShift}, ${greenShift}, ${blueShift})`;
        shadowColor = `rgb(${redShift - 30}, ${greenShift - 25}, ${blueShift - 20})`;
        highlightColor = `rgb(${Math.min(255, redShift + 30)}, ${Math.min(255, greenShift + 25)}, ${Math.min(255, blueShift + 20)})`;
      }

      // Mountain gradient
      const mountainGradient = ctx.createLinearGradient(
        mountain.x, peakY,
        mountain.x + mountain.width, mountain.baseY
      );
      mountainGradient.addColorStop(0, highlightColor);
      mountainGradient.addColorStop(0.4, baseColor);
      mountainGradient.addColorStop(1, shadowColor);

      ctx.fillStyle = mountainGradient;
      ctx.beginPath();
      ctx.moveTo(mountain.x, height);
      ctx.lineTo(mountain.x, mountain.baseY);

      // Left slope
      ctx.lineTo(peakX - mountain.width * 0.1, peakY + mountain.height * 0.3);
      ctx.lineTo(peakX, peakY);

      // Right slope
      ctx.lineTo(peakX + mountain.width * 0.15, peakY + mountain.height * 0.25);
      ctx.lineTo(mountain.x + mountain.width, mountain.baseY);
      ctx.lineTo(mountain.x + mountain.width, height);
      ctx.closePath();
      ctx.fill();

      // Snow cap
      if (mountain.snowCap) {
        const snowGradient = ctx.createLinearGradient(peakX, peakY, peakX, peakY + mountain.height * 0.3);
        snowGradient.addColorStop(0, darkMode ? '#C8D0D8' : '#FFFFFF');
        snowGradient.addColorStop(1, 'transparent');

        ctx.fillStyle = snowGradient;
        ctx.beginPath();
        ctx.moveTo(peakX - mountain.width * 0.08, peakY + mountain.height * 0.25);
        ctx.lineTo(peakX, peakY);
        ctx.lineTo(peakX + mountain.width * 0.1, peakY + mountain.height * 0.2);
        ctx.quadraticCurveTo(peakX, peakY + mountain.height * 0.3, peakX - mountain.width * 0.08, peakY + mountain.height * 0.25);
        ctx.fill();
      }

      // Ridge lines
      ctx.strokeStyle = shadowColor;
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.3;
      ctx.beginPath();
      ctx.moveTo(peakX, peakY);
      ctx.lineTo(peakX - mountain.width * 0.2, mountain.baseY);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(peakX, peakY);
      ctx.lineTo(peakX + mountain.width * 0.25, mountain.baseY);
      ctx.stroke();
      ctx.globalAlpha = 1;
    };

    const drawMistLayer = (ctx: CanvasRenderingContext2D, mist: MistLayer, width: number) => {
      const mistOffset = Math.sin(timeRef * 0.001 + mist.offset) * 50;

      ctx.fillStyle = darkMode
        ? `rgba(60, 80, 100, ${mist.density})`
        : `rgba(255, 255, 255, ${mist.density})`;

      ctx.beginPath();
      ctx.moveTo(0, mist.y + mist.height);

      for (let x = 0; x <= width; x += 20) {
        const waveY = mist.y +
          Math.sin((x + mistOffset) * 0.01 + timeRef * mist.speed * 0.01) * mist.height * 0.5;
        ctx.lineTo(x, waveY);
      }

      ctx.lineTo(width, mist.y + mist.height);
      ctx.closePath();
      ctx.fill();
    };

    const drawTree = (ctx: CanvasRenderingContext2D, tree: Tree) => {
      // Atmospheric perspective based on layer
      const alphaMultiplier = 1 - tree.layer * 0.2;

      ctx.save();
      ctx.translate(tree.x, tree.y);
      ctx.globalAlpha = alphaMultiplier;

      const treeColor = darkMode
        ? `rgb(${20 + tree.layer * 15}, ${30 + tree.layer * 15}, ${25 + tree.layer * 15})`
        : `rgb(${30 + tree.layer * 20}, ${50 + tree.layer * 20}, ${35 + tree.layer * 20})`;

      ctx.fillStyle = treeColor;

      if (tree.type === 'pine') {
        // Trunk
        ctx.fillRect(-tree.height * 0.04, -tree.height * 0.15, tree.height * 0.08, tree.height * 0.15);

        // Layers
        for (let layer = 0; layer < 4; layer++) {
          const layerY = -tree.height * 0.15 - layer * tree.height * 0.22;
          const layerWidth = tree.height * (0.35 - layer * 0.06);
          ctx.beginPath();
          ctx.moveTo(-layerWidth, layerY);
          ctx.lineTo(0, layerY - tree.height * 0.18);
          ctx.lineTo(layerWidth, layerY);
          ctx.closePath();
          ctx.fill();
        }
      } else if (tree.type === 'fir') {
        // Trunk
        ctx.fillRect(-tree.height * 0.03, -tree.height * 0.1, tree.height * 0.06, tree.height * 0.1);

        // Triangular shape
        ctx.beginPath();
        ctx.moveTo(-tree.height * 0.25, -tree.height * 0.1);
        ctx.lineTo(0, -tree.height);
        ctx.lineTo(tree.height * 0.25, -tree.height * 0.1);
        ctx.closePath();
        ctx.fill();
      } else {
        // Bare tree
        ctx.strokeStyle = treeColor;
        ctx.lineWidth = tree.height * 0.05;
        ctx.lineCap = 'round';

        // Trunk
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(0, -tree.height * 0.6);
        ctx.stroke();

        // Branches
        ctx.lineWidth = tree.height * 0.03;
        const branches = [
          { start: 0.3, angle: -0.5, len: 0.3 },
          { start: 0.4, angle: 0.4, len: 0.35 },
          { start: 0.55, angle: -0.6, len: 0.25 },
          { start: 0.65, angle: 0.5, len: 0.2 },
        ];

        branches.forEach(b => {
          ctx.beginPath();
          ctx.moveTo(0, -tree.height * b.start);
          ctx.lineTo(
            Math.cos(b.angle - Math.PI / 2) * tree.height * b.len,
            -tree.height * b.start + Math.sin(b.angle - Math.PI / 2) * tree.height * b.len
          );
          ctx.stroke();
        });
      }

      ctx.restore();
    };

    const drawBird = (ctx: CanvasRenderingContext2D, bird: Bird) => {
      const wingAngle = Math.sin(timeRef * 0.15 + bird.wingPhase) * 0.5;

      ctx.save();
      ctx.translate(bird.x, bird.y);
      ctx.scale(bird.direction, 1);

      ctx.strokeStyle = darkMode ? '#2a3a4a' : '#333';
      ctx.lineWidth = 1.5;
      ctx.lineCap = 'round';

      // Wings
      ctx.beginPath();
      ctx.moveTo(-bird.size, wingAngle * bird.size);
      ctx.quadraticCurveTo(0, -wingAngle * bird.size * 0.5, bird.size, wingAngle * bird.size);
      ctx.stroke();

      ctx.restore();
    };

    const drawWaterReflection = (ctx: CanvasRenderingContext2D, water: WaterReflection, width: number, height: number) => {
      const waterGradient = ctx.createLinearGradient(0, water.y, 0, height);
      if (darkMode) {
        waterGradient.addColorStop(0, 'rgba(30, 50, 70, 0.6)');
        waterGradient.addColorStop(0.5, 'rgba(20, 40, 60, 0.8)');
        waterGradient.addColorStop(1, 'rgba(10, 30, 50, 0.9)');
      } else {
        waterGradient.addColorStop(0, 'rgba(135, 180, 220, 0.6)');
        waterGradient.addColorStop(0.5, 'rgba(100, 150, 200, 0.7)');
        waterGradient.addColorStop(1, 'rgba(80, 130, 180, 0.8)');
      }

      ctx.fillStyle = waterGradient;
      ctx.fillRect(0, water.y, width, height - water.y);

      // Ripple effect
      ctx.strokeStyle = darkMode ? 'rgba(100, 140, 180, 0.2)' : 'rgba(255, 255, 255, 0.3)';
      ctx.lineWidth = 1;

      for (let i = 0; i < 8; i++) {
        const rippleY = water.y + 10 + i * 15;
        const phase = timeRef * 0.005 + i * 0.5;

        ctx.beginPath();
        for (let x = 0; x <= width; x += 10) {
          const y = rippleY + Math.sin(x * 0.02 + phase) * 2;
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }

      // Mountain reflections (simplified)
      ctx.globalAlpha = 0.2;
      ctx.save();
      ctx.translate(0, water.y * 2);
      ctx.scale(1, -0.3);
      mountains.filter(m => m.layer < 2).forEach(m => {
        drawMountain(ctx, m, height);
      });
      ctx.restore();
      ctx.globalAlpha = 1;
    };

    const animate = () => {
      const { width, height } = canvas;
      timeRef++;

      ctx.clearRect(0, 0, width, height);

      // Draw sky
      drawSky(ctx, width, height);

      // Draw clouds
      clouds.forEach(cloud => {
        cloud.x += cloud.speed;
        if (cloud.x > width + 100) {
          cloud.x = -100;
        }
        drawCloud(ctx, cloud);
      });

      // Draw mountains (sorted by layer)
      mountains.sort((a, b) => a.layer - b.layer);
      mountains.forEach(mountain => drawMountain(ctx, mountain, height));

      // Draw trees between mountain layers
      trees.forEach(tree => drawTree(ctx, tree));

      // Draw mist layers
      mistLayers.forEach(mist => drawMistLayer(ctx, mist, width));

      // Draw water reflection
      if (waterReflection) {
        drawWaterReflection(ctx, waterReflection, width, height);
      }

      // Update and draw birds
      birds.forEach(bird => {
        bird.x += bird.speed * bird.direction;
        bird.wingPhase += 0.1;

        // Reset when off screen
        if ((bird.direction === 1 && bird.x > width + 20) ||
            (bird.direction === -1 && bird.x < -20)) {
          bird.x = bird.direction === 1 ? -20 : width + 20;
          bird.y = height * 0.1 + Math.random() * height * 0.25;
        }

        drawBird(ctx, bird);
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
