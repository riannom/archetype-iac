/**
 * Autumn Wind Animation
 * Swirling gusts with occasional leaf bursts.
 * Wind streams carry fallen leaves in dynamic patterns.
 */

import { useEffect, useRef } from 'react';

interface Leaf {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
  type: 'maple' | 'oak' | 'ginkgo' | 'elm';
  color: { r: number; g: number; b: number };
  colorVariation: number; // Random color shift
  opacity: number;
  tumble: number;
  tumbleSpeed: number;
  windInfluence: number;
  curlAmount: number; // Slight curl for realism
}

interface WindGust {
  x: number;
  y: number;
  width: number;
  height: number;
  angle: number;
  strength: number;
  lifetime: number;
  maxLifetime: number;
  particles: WindParticle[];
}

interface WindParticle {
  x: number;
  y: number;
  opacity: number;
  size: number;
}

export function useAutumnWind(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const leavesRef = useRef<Leaf[]>([]);
  const gustsRef = useRef<WindGust[]>([]);
  const animationRef = useRef<number>(0);
  const timeRef = useRef<number>(0);
  const baseWindRef = useRef<{ x: number; y: number }>({ x: 1.5, y: 0.2 });

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const autumnColors = [
      { r: 220, g: 80, b: 40 },   // Red-orange
      { r: 240, g: 140, b: 40 },  // Orange
      { r: 200, g: 160, b: 50 },  // Golden yellow
      { r: 180, g: 60, b: 30 },   // Deep red
      { r: 160, g: 100, b: 40 },  // Brown
      { r: 230, g: 180, b: 60 },  // Bright yellow
      { r: 190, g: 50, b: 50 },   // Crimson
    ];

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeLeaves();
    };

    const initializeLeaves = () => {
      leavesRef.current = [];
      const leafCount = Math.floor((canvas.width * canvas.height) / 20000);

      for (let i = 0; i < leafCount; i++) {
        leavesRef.current.push(createLeaf(true));
      }
    };

    const createLeaf = (randomPos = false): Leaf => {
      const types: ('maple' | 'oak' | 'ginkgo' | 'elm')[] = ['maple', 'oak', 'ginkgo', 'elm'];
      const type = types[Math.floor(Math.random() * types.length)];
      const color = autumnColors[Math.floor(Math.random() * autumnColors.length)];

      return {
        x: randomPos ? Math.random() * canvas.width : -30,
        y: randomPos ? Math.random() * canvas.height : Math.random() * canvas.height * 0.5,
        vx: 0.5 + Math.random() * 1,
        vy: 0.2 + Math.random() * 0.5,
        size: 14 + Math.random() * 16,
        rotation: Math.random() * Math.PI * 2,
        rotationSpeed: (Math.random() - 0.5) * 0.06,
        type,
        color,
        colorVariation: (Math.random() - 0.5) * 30,
        opacity: 0.7 + Math.random() * 0.25,
        tumble: Math.random() * Math.PI * 2,
        tumbleSpeed: 0.02 + Math.random() * 0.04,
        windInfluence: 0.5 + Math.random() * 0.5,
        curlAmount: Math.random() * 0.3,
      };
    };

    const createGust = (): WindGust => {
      const particles: WindParticle[] = [];
      const particleCount = 12 + Math.floor(Math.random() * 15);

      for (let i = 0; i < particleCount; i++) {
        particles.push({
          x: Math.random(),
          y: Math.random(),
          opacity: 0.03 + Math.random() * 0.06,
          size: 1.5 + Math.random() * 2,
        });
      }

      return {
        x: -200,
        y: Math.random() * canvas.height,
        width: 300 + Math.random() * 200,
        height: 100 + Math.random() * 150,
        angle: (Math.random() - 0.5) * 0.2,
        strength: 2 + Math.random() * 3,
        lifetime: 0,
        maxLifetime: 200 + Math.random() * 100,
        particles,
      };
    };

    const drawMapleLeaf = (ctx: CanvasRenderingContext2D, size: number, curl: number) => {
      const s = size * 0.5;
      const c = 1 - curl * 0.3; // Slight asymmetry from curl

      ctx.beginPath();
      ctx.moveTo(0, -s * 1.3);

      // Right side - 5 pointed lobes with serrations
      // Top right lobe
      ctx.lineTo(s * 0.15, -s * 1.1);
      ctx.lineTo(s * 0.7 * c, -s * 1.0);
      ctx.lineTo(s * 0.5 * c, -s * 0.75);
      // Upper right lobe
      ctx.lineTo(s * 1.0 * c, -s * 0.5);
      ctx.lineTo(s * 0.65 * c, -s * 0.35);
      // Middle right lobe
      ctx.lineTo(s * 0.85 * c, s * 0.1);
      ctx.lineTo(s * 0.5 * c, s * 0.15);
      // Lower right lobe
      ctx.lineTo(s * 0.55 * c, s * 0.6);
      ctx.lineTo(s * 0.25 * c, s * 0.5);
      // Stem connection
      ctx.lineTo(s * 0.08, s * 0.7);
      ctx.lineTo(0, s * 1.1);

      // Left side - mirror with slight variation
      ctx.lineTo(-s * 0.08, s * 0.7);
      ctx.lineTo(-s * 0.25, s * 0.5);
      ctx.lineTo(-s * 0.55, s * 0.6);
      ctx.lineTo(-s * 0.5, s * 0.15);
      ctx.lineTo(-s * 0.85, s * 0.1);
      ctx.lineTo(-s * 0.65, -s * 0.35);
      ctx.lineTo(-s * 1.0, -s * 0.5);
      ctx.lineTo(-s * 0.5, -s * 0.75);
      ctx.lineTo(-s * 0.7, -s * 1.0);
      ctx.lineTo(-s * 0.15, -s * 1.1);

      ctx.closePath();
    };

    const drawOakLeaf = (ctx: CanvasRenderingContext2D, size: number, curl: number) => {
      const s = size * 0.5;

      ctx.beginPath();
      ctx.moveTo(0, -s * 1.0);

      // Right side - rounded lobes characteristic of oak
      ctx.quadraticCurveTo(s * 0.2, -s * 0.9, s * 0.35, -s * 0.75);
      ctx.quadraticCurveTo(s * 0.55, -s * 0.8, s * 0.6, -s * 0.55);
      ctx.quadraticCurveTo(s * 0.45, -s * 0.5, s * 0.35, -s * 0.4);
      ctx.quadraticCurveTo(s * 0.55, -s * 0.35, s * 0.65, -s * 0.1);
      ctx.quadraticCurveTo(s * 0.5, -s * 0.05, s * 0.4, s * 0.05);
      ctx.quadraticCurveTo(s * 0.6, s * 0.15, s * 0.6, s * 0.35);
      ctx.quadraticCurveTo(s * 0.45, s * 0.4, s * 0.3, s * 0.45);
      ctx.quadraticCurveTo(s * 0.4, s * 0.6, s * 0.35, s * 0.75);
      ctx.quadraticCurveTo(s * 0.2, s * 0.7, s * 0.1, s * 0.8);
      ctx.lineTo(0, s * 1.1);

      // Left side - mirror
      ctx.lineTo(-s * 0.1, s * 0.8);
      ctx.quadraticCurveTo(-s * 0.2, s * 0.7, -s * 0.35, s * 0.75);
      ctx.quadraticCurveTo(-s * 0.4, s * 0.6, -s * 0.3, s * 0.45);
      ctx.quadraticCurveTo(-s * 0.45, s * 0.4, -s * 0.6, s * 0.35);
      ctx.quadraticCurveTo(-s * 0.6, s * 0.15, -s * 0.4, s * 0.05);
      ctx.quadraticCurveTo(-s * 0.5, -s * 0.05, -s * 0.65, -s * 0.1);
      ctx.quadraticCurveTo(-s * 0.55, -s * 0.35, -s * 0.35, -s * 0.4);
      ctx.quadraticCurveTo(-s * 0.45, -s * 0.5, -s * 0.6, -s * 0.55);
      ctx.quadraticCurveTo(-s * 0.55, -s * 0.8, -s * 0.35, -s * 0.75);
      ctx.quadraticCurveTo(-s * 0.2, -s * 0.9, 0, -s * 1.0);

      ctx.closePath();
    };

    const drawGinkgoLeaf = (ctx: CanvasRenderingContext2D, size: number, curl: number) => {
      const s = size * 0.5;

      ctx.beginPath();
      // Fan-shaped ginkgo leaf with notch
      ctx.moveTo(0, s * 1.0);
      // Right edge - gentle curve outward
      ctx.quadraticCurveTo(s * 0.15, s * 0.3, s * 0.8, -s * 0.5);
      // Wavy top edge with central notch
      ctx.quadraticCurveTo(s * 0.9, -s * 0.7, s * 0.6, -s * 0.85);
      ctx.quadraticCurveTo(s * 0.3, -s * 0.95, s * 0.1, -s * 0.8);
      // Central notch
      ctx.lineTo(0, -s * 0.6);
      ctx.lineTo(-s * 0.1, -s * 0.8);
      ctx.quadraticCurveTo(-s * 0.3, -s * 0.95, -s * 0.6, -s * 0.85);
      ctx.quadraticCurveTo(-s * 0.9, -s * 0.7, -s * 0.8, -s * 0.5);
      // Left edge
      ctx.quadraticCurveTo(-s * 0.15, s * 0.3, 0, s * 1.0);

      ctx.closePath();
    };

    const drawElmLeaf = (ctx: CanvasRenderingContext2D, size: number, curl: number) => {
      const s = size * 0.5;

      ctx.beginPath();
      ctx.moveTo(0, -s * 1.1);

      // Asymmetric elm leaf with serrated edges
      // Right side with small serrations
      const serrationsRight = 6;
      for (let i = 0; i < serrationsRight; i++) {
        const t = i / serrationsRight;
        const y = -s * 1.0 + t * s * 1.8;
        const baseWidth = Math.sin(t * Math.PI) * s * 0.7;
        const serration = (i % 2 === 0 ? 0.08 : 0) * s;
        ctx.lineTo(baseWidth + serration, y);
      }
      ctx.lineTo(s * 0.05, s * 0.9);
      ctx.lineTo(0, s * 1.1);

      // Left side
      ctx.lineTo(-s * 0.05, s * 0.9);
      for (let i = serrationsRight - 1; i >= 0; i--) {
        const t = i / serrationsRight;
        const y = -s * 1.0 + t * s * 1.8;
        const baseWidth = Math.sin(t * Math.PI) * s * 0.65;
        const serration = (i % 2 === 0 ? 0.08 : 0) * s;
        ctx.lineTo(-(baseWidth + serration), y);
      }

      ctx.closePath();
    };

    const drawLeaf = (leaf: Leaf, opacityMult: number) => {
      ctx.save();
      ctx.translate(leaf.x, leaf.y);
      ctx.rotate(leaf.rotation);

      // 3D tumble effect
      const tumbleFactor = Math.cos(leaf.tumble);
      ctx.scale(0.4 + Math.abs(tumbleFactor) * 0.6, 1);

      const alpha = leaf.opacity * opacityMult;
      const v = leaf.colorVariation;
      const { r, g, b } = leaf.color;

      // Draw leaf shape based on type
      switch (leaf.type) {
        case 'maple':
          drawMapleLeaf(ctx, leaf.size, leaf.curlAmount);
          break;
        case 'oak':
          drawOakLeaf(ctx, leaf.size, leaf.curlAmount);
          break;
        case 'ginkgo':
          drawGinkgoLeaf(ctx, leaf.size, leaf.curlAmount);
          break;
        case 'elm':
          drawElmLeaf(ctx, leaf.size, leaf.curlAmount);
          break;
      }

      // Fill with natural gradient - variation adds uniqueness
      const gradient = ctx.createLinearGradient(0, -leaf.size * 0.5, 0, leaf.size * 0.5);
      gradient.addColorStop(0, `rgba(${Math.min(255, r + 20 + v)}, ${Math.min(255, g + 15 + v * 0.5)}, ${Math.min(255, b + 10)}, ${alpha})`);
      gradient.addColorStop(0.5, `rgba(${r + v * 0.3}, ${g + v * 0.2}, ${b}, ${alpha})`);
      gradient.addColorStop(1, `rgba(${Math.max(0, r - 25 + v * 0.2)}, ${Math.max(0, g - 20)}, ${Math.max(0, b - 15)}, ${alpha * 0.9})`);

      ctx.fillStyle = gradient;
      ctx.fill();

      // Subtle edge highlight
      ctx.strokeStyle = `rgba(${Math.min(255, r + 40)}, ${Math.min(255, g + 30)}, ${Math.min(255, b + 20)}, ${alpha * 0.2})`;
      ctx.lineWidth = 0.5;
      ctx.stroke();

      // Central vein - more prominent
      const veinColor = `rgba(${Math.max(0, r - 50)}, ${Math.max(0, g - 40)}, ${Math.max(0, b - 25)}, ${alpha * 0.5})`;
      ctx.strokeStyle = veinColor;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(0, -leaf.size * 0.45);
      ctx.lineTo(0, leaf.size * 0.5);
      ctx.stroke();

      // Secondary veins for maple and oak
      if (leaf.type === 'maple' || leaf.type === 'oak') {
        ctx.lineWidth = 0.4;
        ctx.strokeStyle = `rgba(${Math.max(0, r - 40)}, ${Math.max(0, g - 30)}, ${Math.max(0, b - 20)}, ${alpha * 0.3})`;
        // Left veins
        ctx.beginPath();
        ctx.moveTo(0, -leaf.size * 0.2);
        ctx.lineTo(-leaf.size * 0.3, -leaf.size * 0.35);
        ctx.moveTo(0, leaf.size * 0.1);
        ctx.lineTo(-leaf.size * 0.25, leaf.size * 0.25);
        // Right veins
        ctx.moveTo(0, -leaf.size * 0.2);
        ctx.lineTo(leaf.size * 0.3, -leaf.size * 0.35);
        ctx.moveTo(0, leaf.size * 0.1);
        ctx.lineTo(leaf.size * 0.25, leaf.size * 0.25);
        ctx.stroke();
      }

      ctx.restore();
    };

    const drawGust = (gust: WindGust, opacityMult: number) => {
      const progress = gust.lifetime / gust.maxLifetime;
      const fadeIn = Math.min(1, progress * 6);
      const fadeOut = Math.max(0, 1 - (progress - 0.75) * 4);
      const gustOpacity = fadeIn * fadeOut;

      ctx.save();
      ctx.translate(gust.x, gust.y);
      ctx.rotate(gust.angle);

      // Draw subtle wind particles - very faint
      gust.particles.forEach((particle) => {
        const px = particle.x * gust.width;
        const py = (particle.y - 0.5) * gust.height;
        const alpha = particle.opacity * gustOpacity * opacityMult * 0.5;

        const windColor = darkMode
          ? `rgba(180, 180, 195, ${alpha})`
          : `rgba(160, 160, 175, ${alpha})`;

        ctx.fillStyle = windColor;
        ctx.beginPath();
        ctx.ellipse(px, py, particle.size * 2.5, particle.size * 0.6, 0, 0, Math.PI * 2);
        ctx.fill();
      });

      // Very subtle wind streak lines
      ctx.strokeStyle = darkMode
        ? `rgba(180, 180, 195, ${gustOpacity * 0.04 * opacityMult})`
        : `rgba(160, 160, 175, ${gustOpacity * 0.04 * opacityMult})`;
      ctx.lineWidth = 0.5;

      for (let i = 0; i < 3; i++) {
        const y = (i / 2 - 0.5) * gust.height * 0.6;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.bezierCurveTo(
          gust.width * 0.3, y + 6,
          gust.width * 0.6, y - 6,
          gust.width, y
        );
        ctx.stroke();
      }

      ctx.restore();
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const opacityMult = opacity / 50;

      // Vary base wind
      baseWindRef.current.x = 1.5 + Math.sin(time * 0.001) * 0.5;
      baseWindRef.current.y = 0.2 + Math.sin(time * 0.0015) * 0.3;

      // Spawn gusts occasionally
      if (Math.random() < 0.008) {
        gustsRef.current.push(createGust());
      }

      // Update and draw gusts
      gustsRef.current = gustsRef.current.filter((gust) => {
        gust.lifetime++;
        gust.x += gust.strength * 2;

        // Move particles within gust
        gust.particles.forEach((p) => {
          p.x += 0.01;
          if (p.x > 1) p.x = 0;
        });

        if (gust.lifetime > gust.maxLifetime || gust.x > canvas.width + gust.width) {
          return false;
        }

        drawGust(gust, opacityMult);
        return true;
      });

      // Update and draw leaves
      leavesRef.current.forEach((leaf, index) => {
        // Apply base wind
        leaf.vx += (baseWindRef.current.x - leaf.vx) * 0.02 * leaf.windInfluence;
        leaf.vy += (baseWindRef.current.y + 0.5 - leaf.vy) * 0.02;

        // Check if leaf is in a gust
        gustsRef.current.forEach((gust) => {
          const progress = gust.lifetime / gust.maxLifetime;
          const gustActive = progress > 0.1 && progress < 0.9;

          if (gustActive) {
            // Transform leaf position to gust space
            const dx = leaf.x - gust.x;
            const dy = leaf.y - gust.y;
            const rotatedX = dx * Math.cos(-gust.angle) - dy * Math.sin(-gust.angle);
            const rotatedY = dx * Math.sin(-gust.angle) + dy * Math.cos(-gust.angle);

            if (rotatedX > 0 && rotatedX < gust.width &&
                Math.abs(rotatedY) < gust.height / 2) {
              // Leaf is in gust - apply force
              leaf.vx += gust.strength * 0.3 * leaf.windInfluence;
              leaf.vy += Math.sin(gust.angle) * gust.strength * 0.1;
              leaf.rotationSpeed += (Math.random() - 0.5) * 0.02;
            }
          }
        });

        // Apply velocity
        leaf.x += leaf.vx;
        leaf.y += leaf.vy;

        // Update rotation and tumble
        leaf.rotation += leaf.rotationSpeed;
        leaf.tumble += leaf.tumbleSpeed;

        // Dampen rotation
        leaf.rotationSpeed *= 0.99;

        // Reset if off screen
        if (leaf.x > canvas.width + 50 || leaf.y > canvas.height + 50) {
          leavesRef.current[index] = createLeaf(false);
        }

        drawLeaf(leaf, opacityMult);
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
