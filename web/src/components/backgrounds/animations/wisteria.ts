/**
 * Wisteria Animation
 *
 * Beautiful cascading wisteria flowers hanging from above,
 * gently swaying in the breeze with falling petals.
 * Enhanced with floating light particles, depth layering,
 * and water reflection.
 */

import { useEffect, useRef } from 'react';

interface WisteriaCluster {
  x: number;
  y: number;
  length: number;
  width: number;
  swayPhase: number;
  swaySpeed: number;
  swayAmount: number;
  color: string;
  bloomPhase: number;
  flowerCount: number;
  depth: number; // 0 = far background, 1 = foreground
}

interface FallingPetal {
  x: number;
  y: number;
  size: number;
  color: string;
  rotation: number;
  rotationSpeed: number;
  speedX: number;
  speedY: number;
  swayPhase: number;
  swaySpeed: number;
  opacity: number;
}

interface Vine {
  startX: number;
  startY: number;
  controlPoints: { x: number; y: number }[];
  swayPhase: number;
  swaySpeed: number;
}

interface LightParticle {
  x: number;
  y: number;
  size: number;
  speedX: number;
  speedY: number;
  phase: number;
  phaseSpeed: number;
  opacity: number;
}

export function useWisteria(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  enabled: boolean
) {
  const clustersRef = useRef<WisteriaCluster[]>([]);
  const petalsRef = useRef<FallingPetal[]>([]);
  const vinesRef = useRef<Vine[]>([]);
  const particlesRef = useRef<LightParticle[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef(0);

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
    const waterLevel = height * 0.82;

    // Wisteria colors (purples and lavenders)
    const wisteriaColors = darkMode
      ? ['#8060a0', '#7050a0', '#9070b0', '#6050a0', '#a080c0', '#7060b0']
      : ['#b090d0', '#a080c0', '#c0a0e0', '#9080c0', '#d0b0f0', '#a090d0'];

    // Initialize wisteria clusters with depth layering
    clustersRef.current = [];

    const clusterCount = Math.floor(width / 50);
    for (let i = 0; i < clusterCount; i++) {
      const baseX = (i / clusterCount) * width + Math.random() * 40 - 20;
      const depth = Math.random(); // Random depth for each hanging point

      const subClusters = 2 + Math.floor(Math.random() * 3);
      for (let j = 0; j < subClusters; j++) {
        // Depth affects size and length - made bigger
        const depthScale = 0.6 + depth * 0.5;
        const clusterLength = (100 + Math.random() * 140) * depthScale;

        clustersRef.current.push({
          x: baseX + (Math.random() - 0.5) * 30,
          y: -10 + Math.random() * 30,
          length: clusterLength,
          width: (18 + Math.random() * 12) * depthScale,
          swayPhase: Math.random() * Math.PI * 2,
          swaySpeed: 0.001 + Math.random() * 0.001,
          swayAmount: (3 + Math.random() * 3) * depthScale,
          color: wisteriaColors[Math.floor(Math.random() * wisteriaColors.length)],
          bloomPhase: Math.random() * Math.PI * 2,
          flowerCount: Math.floor((35 + Math.random() * 25) * depthScale),
          depth,
        });
      }
    }

    // Sort clusters by depth (draw background first)
    clustersRef.current.sort((a, b) => a.depth - b.depth);

    // Initialize vines
    vinesRef.current = [];
    for (let i = 0; i < 6; i++) {
      const startX = Math.random() * width;
      const controlPoints = [];
      let currentY = -20;

      for (let j = 0; j < 4; j++) {
        currentY += 40 + Math.random() * 60;
        controlPoints.push({
          x: startX + (Math.random() - 0.5) * 100,
          y: currentY,
        });
      }

      vinesRef.current.push({
        startX,
        startY: -20,
        controlPoints,
        swayPhase: Math.random() * Math.PI * 2,
        swaySpeed: 0.002 + Math.random() * 0.001,
      });
    }

    // Initialize floating light particles
    particlesRef.current = [];
    for (let i = 0; i < 25; i++) {
      particlesRef.current.push(createLightParticle(width, height));
    }

    petalsRef.current = [];

    function createLightParticle(w: number, h: number): LightParticle {
      return {
        x: Math.random() * w,
        y: Math.random() * h * 0.7,
        size: 2 + Math.random() * 3,
        speedX: (Math.random() - 0.5) * 0.08,
        speedY: (Math.random() - 0.5) * 0.05,
        phase: Math.random() * Math.PI * 2,
        phaseSpeed: 0.003 + Math.random() * 0.003,
        opacity: 0.4 + Math.random() * 0.3,
      };
    }

    // Draw teardrop/bell-shaped wisteria flower
    const drawFlower = (
      ctx: CanvasRenderingContext2D,
      x: number,
      y: number,
      size: number,
      r: number,
      g: number,
      b: number,
      alpha: number
    ) => {
      ctx.save();
      ctx.translate(x, y);

      // Bell/teardrop shape using bezier curves
      ctx.beginPath();
      ctx.moveTo(0, -size * 0.3);
      // Left curve
      ctx.bezierCurveTo(
        -size * 0.6, -size * 0.1,
        -size * 0.5, size * 0.6,
        0, size * 0.8
      );
      // Right curve
      ctx.bezierCurveTo(
        size * 0.5, size * 0.6,
        size * 0.6, -size * 0.1,
        0, -size * 0.3
      );
      ctx.closePath();

      // Gradient fill
      const gradient = ctx.createRadialGradient(0, 0, 0, 0, size * 0.3, size);
      gradient.addColorStop(0, `rgba(${r + 40}, ${g + 40}, ${b + 40}, ${alpha})`);
      gradient.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${alpha * 0.9})`);
      gradient.addColorStop(1, `rgba(${r - 20}, ${g - 20}, ${b}, ${alpha * 0.7})`);

      ctx.fillStyle = gradient;
      ctx.fill();

      ctx.restore();
    };

    const drawWisteriaCluster = (
      ctx: CanvasRenderingContext2D,
      cluster: WisteriaCluster
    ) => {
      const sway = Math.sin(cluster.swayPhase) * cluster.swayAmount;
      const baseColor = cluster.color;

      // Parse color for variations
      const r = parseInt(baseColor.slice(1, 3), 16);
      const g = parseInt(baseColor.slice(3, 5), 16);
      const b = parseInt(baseColor.slice(5, 7), 16);

      // Depth affects opacity (further = more faded)
      const depthAlpha = 0.4 + cluster.depth * 0.6;

      // Draw individual flowers in the cluster
      // Pre-calculate stable random positions for this cluster
      const seed = cluster.x * 1000 + cluster.y;
      for (let i = 0; i < cluster.flowerCount; i++) {
        const progress = i / cluster.flowerCount;
        const y = cluster.y + progress * cluster.length;

        // Cluster tapers toward bottom
        const clusterWidthAtY = cluster.width * (1 - progress * 0.6);

        // Flowers get bigger (increased base size)
        const flowerSize = (5 + ((seed + i * 17) % 30) / 10) * (1 - progress * 0.3);

        // Horizontal offset with sway - use deterministic offset
        const swayAtY = sway * progress;
        const stableOffset = ((seed + i * 31) % 100) / 100 - 0.5;
        const xOffset = stableOffset * clusterWidthAtY * 2;
        const x = cluster.x + xOffset + swayAtY;

        // Color variation (lighter at tips)
        const brightness = 1 + progress * 0.2;
        const flowerR = Math.min(255, r * brightness);
        const flowerG = Math.min(255, g * brightness);
        const flowerB = Math.min(255, b * brightness);

        // Bloom animation - very subtle
        const bloomScale = 1 + Math.sin(cluster.bloomPhase + progress * Math.PI) * 0.015;

        // Stable opacity based on position (no random per frame!)
        const stableOpacity = 0.75 + ((seed + i * 13) % 25) / 100;

        drawFlower(
          ctx,
          x,
          y,
          flowerSize * bloomScale,
          flowerR,
          flowerG,
          flowerB,
          depthAlpha * stableOpacity
        );

        // Occasional petal drop (only from foreground clusters)
        if (cluster.depth > 0.5 && Math.random() < 0.0002) {
          petalsRef.current.push({
            x,
            y,
            size: 2 + Math.random() * 2,
            color: `rgba(${flowerR}, ${flowerG}, ${flowerB}, 0.8)`,
            rotation: Math.random() * Math.PI * 2,
            rotationSpeed: (Math.random() - 0.5) * 0.015,
            speedX: (Math.random() - 0.5) * 0.2,
            speedY: 0.2 + Math.random() * 0.2,
            swayPhase: Math.random() * Math.PI * 2,
            swaySpeed: 0.01 + Math.random() * 0.008,
            opacity: 1,
          });
        }
      }

      // Draw stem (only for foreground clusters)
      if (cluster.depth > 0.6) {
        ctx.beginPath();
        ctx.moveTo(cluster.x, cluster.y);
        ctx.lineTo(cluster.x + sway * 0.2, cluster.y + 15);
        ctx.strokeStyle = darkMode ? '#4a5040' : '#6a8060';
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    };

    const drawVine = (ctx: CanvasRenderingContext2D, vine: Vine) => {
      const sway = Math.sin(vine.swayPhase) * 10;

      ctx.beginPath();
      ctx.moveTo(vine.startX, vine.startY);

      for (let i = 0; i < vine.controlPoints.length; i++) {
        const cp = vine.controlPoints[i];
        const swayAtPoint = sway * (i / vine.controlPoints.length);

        if (i === 0) {
          ctx.quadraticCurveTo(
            vine.startX + swayAtPoint,
            (vine.startY + cp.y) / 2,
            cp.x + swayAtPoint,
            cp.y
          );
        } else {
          const prev = vine.controlPoints[i - 1];
          ctx.quadraticCurveTo(
            (prev.x + cp.x) / 2 + swayAtPoint,
            (prev.y + cp.y) / 2,
            cp.x + swayAtPoint,
            cp.y
          );
        }
      }

      ctx.strokeStyle = darkMode ? '#3a4030' : '#5a7050';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Leaves
      vine.controlPoints.forEach((cp, i) => {
        const swayAtPoint = sway * (i / vine.controlPoints.length);

        ctx.save();
        ctx.translate(cp.x + swayAtPoint, cp.y);
        ctx.rotate(Math.sin(vine.swayPhase + i) * 0.2);

        ctx.beginPath();
        ctx.ellipse(8, 0, 6, 3, 0.3, 0, Math.PI * 2);
        ctx.fillStyle = darkMode ? '#405038' : '#608050';
        ctx.fill();

        ctx.restore();
      });
    };

    const drawWaterReflection = (
      ctx: CanvasRenderingContext2D,
      currentWidth: number,
      currentHeight: number,
      wLevel: number
    ) => {
      // Water surface
      const waterGradient = ctx.createLinearGradient(0, wLevel, 0, currentHeight);
      if (darkMode) {
        waterGradient.addColorStop(0, 'rgba(20, 25, 40, 0.6)');
        waterGradient.addColorStop(0.3, 'rgba(15, 20, 35, 0.7)');
        waterGradient.addColorStop(1, 'rgba(10, 15, 30, 0.8)');
      } else {
        waterGradient.addColorStop(0, 'rgba(180, 190, 210, 0.5)');
        waterGradient.addColorStop(0.3, 'rgba(160, 175, 200, 0.6)');
        waterGradient.addColorStop(1, 'rgba(140, 160, 190, 0.7)');
      }

      ctx.fillStyle = waterGradient;
      ctx.fillRect(0, wLevel, currentWidth, currentHeight - wLevel);

      // Subtle ripple lines
      ctx.strokeStyle = darkMode
        ? 'rgba(60, 70, 90, 0.3)'
        : 'rgba(200, 210, 230, 0.4)';
      ctx.lineWidth = 0.5;

      for (let i = 0; i < 5; i++) {
        const rippleY = wLevel + 15 + i * 20;
        ctx.beginPath();
        ctx.moveTo(0, rippleY);
        for (let x = 0; x < currentWidth; x += 20) {
          const wave = Math.sin(x * 0.02 + timeRef.current * 0.5 + i) * 2;
          ctx.lineTo(x, rippleY + wave);
        }
        ctx.stroke();
      }

      // Reflected wisteria glow (simplified)
      clustersRef.current.forEach((cluster) => {
        if (cluster.depth > 0.4 && cluster.y + cluster.length > wLevel - 100) {
          const reflectY = wLevel + (wLevel - cluster.y) * 0.3;
          const reflectAlpha = 0.15 * cluster.depth;

          const r = parseInt(cluster.color.slice(1, 3), 16);
          const g = parseInt(cluster.color.slice(3, 5), 16);
          const b = parseInt(cluster.color.slice(5, 7), 16);

          const reflectGradient = ctx.createRadialGradient(
            cluster.x, reflectY, 0,
            cluster.x, reflectY, cluster.length * 0.4
          );
          reflectGradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${reflectAlpha})`);
          reflectGradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);

          ctx.beginPath();
          ctx.ellipse(cluster.x, reflectY, cluster.width * 2, cluster.length * 0.3, 0, 0, Math.PI * 2);
          ctx.fillStyle = reflectGradient;
          ctx.fill();
        }
      });
    };

    const animate = () => {
      const currentWidth = canvas.width;
      const currentHeight = canvas.height;
      const wLevel = currentHeight * 0.82;

      ctx.clearRect(0, 0, currentWidth, currentHeight);
      timeRef.current += 0.016;

      // Background gradient
      const bgGradient = ctx.createLinearGradient(0, 0, 0, wLevel);
      if (darkMode) {
        bgGradient.addColorStop(0, '#12101a');
        bgGradient.addColorStop(0.4, '#1a1525');
        bgGradient.addColorStop(0.7, '#16121f');
        bgGradient.addColorStop(1, '#1a1828');
      } else {
        bgGradient.addColorStop(0, '#e8e0f0');
        bgGradient.addColorStop(0.4, '#e0d8ea');
        bgGradient.addColorStop(0.7, '#e8e0e8');
        bgGradient.addColorStop(1, '#d8d0e0');
      }
      ctx.fillStyle = bgGradient;
      ctx.fillRect(0, 0, currentWidth, wLevel);

      // Soft light from top
      const lightGradient = ctx.createRadialGradient(
        currentWidth * 0.5, -50, 50,
        currentWidth * 0.5, currentHeight * 0.3, currentHeight * 0.5
      );
      if (darkMode) {
        lightGradient.addColorStop(0, 'rgba(100, 80, 120, 0.08)');
        lightGradient.addColorStop(1, 'rgba(60, 50, 80, 0)');
      } else {
        lightGradient.addColorStop(0, 'rgba(255, 245, 255, 0.25)');
        lightGradient.addColorStop(1, 'rgba(240, 230, 250, 0)');
      }
      ctx.fillStyle = lightGradient;
      ctx.fillRect(0, 0, currentWidth, wLevel);

      // Draw vines (behind everything)
      vinesRef.current.forEach((vine) => {
        vine.swayPhase += vine.swaySpeed;
        drawVine(ctx, vine);
      });

      // Draw background clusters first
      clustersRef.current.forEach((cluster) => {
        if (cluster.depth < 0.5) {
          cluster.swayPhase += cluster.swaySpeed;
          cluster.bloomPhase += 0.0005;
          drawWisteriaCluster(ctx, cluster);
        }
      });

      // Draw foreground clusters
      clustersRef.current.forEach((cluster) => {
        if (cluster.depth >= 0.5) {
          cluster.swayPhase += cluster.swaySpeed;
          cluster.bloomPhase += 0.0005;
          drawWisteriaCluster(ctx, cluster);
        }
      });

      // Draw floating light particles
      particlesRef.current.forEach((p) => {
        p.phase += p.phaseSpeed;
        p.x += p.speedX;
        p.y += p.speedY + Math.sin(p.phase) * 0.03;

        // Wrap around
        if (p.x < -20) p.x = currentWidth + 20;
        if (p.x > currentWidth + 20) p.x = -20;
        if (p.y < -20) p.y = currentHeight * 0.6;
        if (p.y > currentHeight * 0.7) p.y = -20;

        // Very subtle pulse (0.85 to 1.0 range)
        const pulse = 0.92 + Math.sin(p.phase) * 0.08;
        const particleGlow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 2);
        particleGlow.addColorStop(0, `rgba(255, 250, 230, ${p.opacity * pulse})`);
        particleGlow.addColorStop(0.4, `rgba(255, 240, 200, ${p.opacity * pulse * 0.5})`);
        particleGlow.addColorStop(1, 'rgba(255, 230, 180, 0)');

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size * 2, 0, Math.PI * 2);
        ctx.fillStyle = particleGlow;
        ctx.fill();

        // Core
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size * 0.5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 240, ${p.opacity * pulse})`;
        ctx.fill();
      });

      // Draw falling petals
      petalsRef.current = petalsRef.current.filter((petal) => {
        petal.swayPhase += petal.swaySpeed;
        petal.x += petal.speedX + Math.sin(petal.swayPhase) * 0.15;
        petal.y += petal.speedY;
        petal.rotation += petal.rotationSpeed;
        petal.opacity -= 0.0008;

        if (petal.y > wLevel || petal.opacity <= 0) return false;

        ctx.save();
        ctx.translate(petal.x, petal.y);
        ctx.rotate(petal.rotation);
        ctx.globalAlpha = petal.opacity;

        // Teardrop petal shape
        ctx.beginPath();
        ctx.moveTo(0, -petal.size);
        ctx.bezierCurveTo(
          -petal.size * 0.6, -petal.size * 0.3,
          -petal.size * 0.4, petal.size * 0.5,
          0, petal.size * 0.6
        );
        ctx.bezierCurveTo(
          petal.size * 0.4, petal.size * 0.5,
          petal.size * 0.6, -petal.size * 0.3,
          0, -petal.size
        );
        ctx.fillStyle = petal.color;
        ctx.fill();

        ctx.restore();
        ctx.globalAlpha = 1;

        return true;
      });

      // Limit petals
      if (petalsRef.current.length > 80) {
        petalsRef.current = petalsRef.current.slice(-60);
      }

      // Draw water reflection
      drawWaterReflection(ctx, currentWidth, currentHeight, wLevel);

      // Canopy shadow at top
      const canopyGradient = ctx.createLinearGradient(0, 0, 0, 50);
      if (darkMode) {
        canopyGradient.addColorStop(0, 'rgba(15, 18, 25, 0.7)');
        canopyGradient.addColorStop(1, 'rgba(15, 18, 25, 0)');
      } else {
        canopyGradient.addColorStop(0, 'rgba(80, 90, 80, 0.25)');
        canopyGradient.addColorStop(1, 'rgba(80, 90, 80, 0)');
      }
      ctx.fillStyle = canopyGradient;
      ctx.fillRect(0, 0, currentWidth, 50);

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
