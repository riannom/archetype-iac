/**
 * Stargazing Animation
 *
 * Peaceful night sky scene with twinkling stars, constellations,
 * occasional shooting stars, and a silhouetted landscape.
 * Stars primarily on sides to keep center clear.
 * All random values pre-generated for smooth, flicker-free animation.
 */

import { useEffect, RefObject } from 'react';

interface Star {
  x: number;
  y: number;
  size: number;
  brightness: number;
  twinkleSpeed: number;
  twinklePhase: number;
  color: string;
}

interface ShootingStar {
  x: number;
  y: number;
  angle: number;
  speed: number;
  length: number;
  life: number;
  maxLife: number;
  isBig?: boolean; // Rare bigger shooting stars
}

interface Constellation {
  stars: { x: number; y: number }[];
  connections: [number, number][];
}

interface CloudPuff {
  offsetX: number;
  offsetY: number;
  size: number;
}

interface Cloud {
  x: number;
  y: number;
  baseSize: number;
  opacity: number;
  targetOpacity: number;
  speed: number;
  puffs: CloudPuff[];
}

interface Tree {
  x: number;
  height: number;
  width: number;
  type: 'pine' | 'deciduous';
}

interface Firefly {
  x: number;
  y: number;
  glowPhase: number;
  targetX: number;
  targetY: number;
  size: number;
}

export function useStargazing(
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
    let stars: Star[] = [];
    let shootingStars: ShootingStar[] = [];
    let constellations: Constellation[] = [];
    let clouds: Cloud[] = [];
    let trees: Tree[] = [];
    let fireflies: Firefly[] = [];
    let timeRef = 0;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeScene();
    };

    const isInSideZone = (x: number, width: number): boolean => {
      const centerStart = width * 0.3;
      const centerEnd = width * 0.7;
      return x < centerStart || x > centerEnd;
    };

    const getRandomSidePosition = (width: number): number => {
      if (Math.random() < 0.7) {
        return Math.random() < 0.5
          ? Math.random() * width * 0.3
          : width * 0.7 + Math.random() * width * 0.3;
      }
      return Math.random() * width;
    };

    // Pre-generate cloud puff positions
    const generateCloudPuffs = (baseSize: number): CloudPuff[] => {
      const puffs: CloudPuff[] = [];
      for (let i = 0; i < 5; i++) {
        puffs.push({
          offsetX: (i - 2) * baseSize * 0.3,
          offsetY: Math.sin(i * 1.5) * 10,
          size: baseSize * (0.3 + Math.random() * 0.3),
        });
      }
      return puffs;
    };

    const initializeScene = () => {
      const { width, height } = canvas;

      // Create stars with bias towards sides
      stars = [];
      const starCount = Math.floor((width * height) / 8000);
      const starColors = ['#FFFFFF', '#FFFACD', '#ADD8E6', '#FFE4E1', '#E6E6FA'];

      for (let i = 0; i < starCount; i++) {
        const x = getRandomSidePosition(width);
        // Bias stars towards upper portion
        const y = Math.random() * height * 0.7;

        stars.push({
          x,
          y,
          size: isInSideZone(x, width) ? 0.5 + Math.random() * 2 : 0.3 + Math.random() * 1,
          brightness: 0.5 + Math.random() * 0.5,
          twinkleSpeed: 0.015 + Math.random() * 0.02,
          twinklePhase: Math.random() * Math.PI * 2,
          color: starColors[Math.floor(Math.random() * starColors.length)],
        });
      }

      // Create constellations based on real star positions (RA/Dec → screen coords)
      constellations = [];

      if (width > 600) {
        // Orion — left side (proportions from actual RA/Dec)
        constellations.push({
          stars: [
            { x: width * 0.03,  y: height * 0.06  },  // Betelgeuse (left shoulder)
            { x: width * 0.176, y: height * 0.087 },  // Bellatrix (right shoulder)
            { x: width * 0.103, y: height * 0.301 },  // Alnitak (belt left)
            { x: width * 0.123, y: height * 0.282 },  // Alnilam (belt center)
            { x: width * 0.142, y: height * 0.258 },  // Mintaka (belt right)
            { x: width * 0.069, y: height * 0.50  },  // Saiph (left foot)
            { x: width * 0.23,  y: height * 0.462 },  // Rigel (right foot)
          ],
          connections: [
            [0, 1],  // shoulders
            [0, 2],  // left torso
            [1, 4],  // right torso
            [2, 3],  // belt
            [3, 4],  // belt
            [2, 5],  // left leg
            [4, 6],  // right leg
          ],
        });

        // Big Dipper (Ursa Major) — right upper (proportions from actual RA/Dec)
        constellations.push({
          stars: [
            { x: width * 0.733, y: height * 0.04  },  // Dubhe (bowl top-left)
            { x: width * 0.73,  y: height * 0.144 },  // Merak (bowl bottom-left)
            { x: width * 0.805, y: height * 0.196 },  // Phecda (bowl bottom-right)
            { x: width * 0.837, y: height * 0.131 },  // Megrez (bowl top-right → handle)
            { x: width * 0.893, y: height * 0.152 },  // Alioth (handle)
            { x: width * 0.935, y: height * 0.172 },  // Mizar (handle)
            { x: width * 0.97,  y: height * 0.28  },  // Alkaid (handle end)
          ],
          connections: [
            [0, 1],  // bowl left
            [1, 2],  // bowl bottom
            [2, 3],  // bowl right
            [3, 0],  // bowl top
            [3, 4],  // handle start
            [4, 5],  // handle middle
            [5, 6],  // handle end
          ],
        });

        // Cassiopeia (W shape) — right lower (proportions from actual RA/Dec)
        constellations.push({
          stars: [
            { x: width * 0.75,  y: height * 0.432 },  // Caph
            { x: width * 0.803, y: height * 0.48  },  // Schedar
            { x: width * 0.831, y: height * 0.404 },  // Gamma Cas (Navi)
            { x: width * 0.880, y: height * 0.413 },  // Ruchbah
            { x: width * 0.93,  y: height * 0.35  },  // Segin
          ],
          connections: [
            [0, 1],  // W segment
            [1, 2],  // W segment
            [2, 3],  // W segment
            [3, 4],  // W segment
          ],
        });
      }

      // Create wispy clouds with pre-generated puffs
      clouds = [];
      for (let i = 0; i < 3; i++) {
        const baseSize = 100 + Math.random() * 150;
        const initialOpacity = 0.05 + Math.random() * 0.08;
        clouds.push({
          x: Math.random() * width,
          y: height * 0.1 + Math.random() * height * 0.3,
          baseSize,
          opacity: initialOpacity,
          targetOpacity: initialOpacity,
          speed: 0.02 + Math.random() * 0.03,
          puffs: generateCloudPuffs(baseSize),
        });
      }

      // Create silhouette trees on sides
      trees = [];
      const treeCount = Math.floor(width / 60);
      for (let i = 0; i < treeCount; i++) {
        const treeX = (i / treeCount) * width;
        if (isInSideZone(treeX, width) || Math.random() < 0.3) {
          trees.push({
            x: treeX + Math.random() * 30 - 15,
            height: 40 + Math.random() * 80,
            width: 20 + Math.random() * 30,
            type: Math.random() < 0.6 ? 'pine' : 'deciduous',
          });
        }
      }

      // Create fireflies at tree level (near ground)
      fireflies = [];
      const fireflyCount = Math.floor(width / 150);
      for (let i = 0; i < fireflyCount; i++) {
        const x = getRandomSidePosition(width);
        fireflies.push({
          x,
          y: height * 0.85 + Math.random() * height * 0.1, // Near ground/trees
          glowPhase: Math.random() * Math.PI * 2,
          targetX: getRandomSidePosition(width),
          targetY: height * 0.85 + Math.random() * height * 0.1,
          size: 0.8 + Math.random() * 0.7, // Much smaller
        });
      }

      shootingStars = [];
    };

    const drawSky = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
      // Night sky gradient
      const skyGradient = ctx.createLinearGradient(0, 0, 0, height);
      if (darkMode) {
        skyGradient.addColorStop(0, '#000510');
        skyGradient.addColorStop(0.4, '#0a1525');
        skyGradient.addColorStop(0.7, '#1a2535');
        skyGradient.addColorStop(1, '#2a3545');
      } else {
        // Twilight mode for light theme
        skyGradient.addColorStop(0, '#1a1a3a');
        skyGradient.addColorStop(0.3, '#2a2a5a');
        skyGradient.addColorStop(0.6, '#3a3a7a');
        skyGradient.addColorStop(1, '#4a4a6a');
      }
      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, width, height);

      // Milky way effect on sides
      const milkyWay = ctx.createLinearGradient(0, 0, width, height * 0.5);
      milkyWay.addColorStop(0, 'rgba(100, 100, 150, 0.1)');
      milkyWay.addColorStop(0.3, 'transparent');
      milkyWay.addColorStop(0.7, 'transparent');
      milkyWay.addColorStop(1, 'rgba(100, 100, 150, 0.1)');
      ctx.fillStyle = milkyWay;
      ctx.fillRect(0, 0, width, height * 0.6);
    };

    const drawStar = (ctx: CanvasRenderingContext2D, star: Star) => {
      const twinkle = Math.sin(timeRef * star.twinkleSpeed + star.twinklePhase) * 0.3 + 0.7;
      const actualBrightness = star.brightness * twinkle;

      // Star glow
      const glowGradient = ctx.createRadialGradient(
        star.x, star.y, 0,
        star.x, star.y, star.size * 3
      );
      glowGradient.addColorStop(0, `rgba(255, 255, 255, ${actualBrightness})`);
      glowGradient.addColorStop(0.3, `rgba(255, 255, 255, ${actualBrightness * 0.3})`);
      glowGradient.addColorStop(1, 'transparent');

      ctx.fillStyle = glowGradient;
      ctx.beginPath();
      ctx.arc(star.x, star.y, star.size * 3, 0, Math.PI * 2);
      ctx.fill();

      // Star core
      ctx.fillStyle = star.color;
      ctx.globalAlpha = actualBrightness;
      ctx.beginPath();
      ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
      ctx.fill();

      // Star points (for brighter stars)
      if (star.size > 1.2) {
        ctx.strokeStyle = star.color;
        ctx.lineWidth = 0.5;
        ctx.globalAlpha = actualBrightness * 0.5;

        // Horizontal line
        ctx.beginPath();
        ctx.moveTo(star.x - star.size * 2, star.y);
        ctx.lineTo(star.x + star.size * 2, star.y);
        ctx.stroke();

        // Vertical line
        ctx.beginPath();
        ctx.moveTo(star.x, star.y - star.size * 2);
        ctx.lineTo(star.x, star.y + star.size * 2);
        ctx.stroke();
      }

      ctx.globalAlpha = 1;
    };

    const drawConstellation = (ctx: CanvasRenderingContext2D, constellation: Constellation) => {
      // Draw connection lines
      ctx.strokeStyle = 'rgba(150, 180, 220, 0.3)';
      ctx.lineWidth = 1;

      constellation.connections.forEach(([from, to]) => {
        const fromStar = constellation.stars[from];
        const toStar = constellation.stars[to];

        ctx.beginPath();
        ctx.moveTo(fromStar.x, fromStar.y);
        ctx.lineTo(toStar.x, toStar.y);
        ctx.stroke();
      });

      // Draw constellation stars (brighter)
      constellation.stars.forEach(star => {
        const twinkle = Math.sin(timeRef * 0.015 + star.x) * 0.2 + 0.8;

        // Glow
        const glowGradient = ctx.createRadialGradient(
          star.x, star.y, 0,
          star.x, star.y, 8
        );
        glowGradient.addColorStop(0, `rgba(200, 220, 255, ${twinkle})`);
        glowGradient.addColorStop(0.5, `rgba(200, 220, 255, ${twinkle * 0.3})`);
        glowGradient.addColorStop(1, 'transparent');

        ctx.fillStyle = glowGradient;
        ctx.beginPath();
        ctx.arc(star.x, star.y, 8, 0, Math.PI * 2);
        ctx.fill();

        // Core
        ctx.fillStyle = '#FFFFFF';
        ctx.beginPath();
        ctx.arc(star.x, star.y, 2, 0, Math.PI * 2);
        ctx.fill();
      });
    };

    const drawShootingStar = (ctx: CanvasRenderingContext2D, ss: ShootingStar) => {
      const lifeRatio = ss.life / ss.maxLife;
      const lineWidth = ss.isBig ? 4 : 2;
      const headRadius = ss.isBig ? 12 : 5;

      ctx.save();
      ctx.translate(ss.x, ss.y);
      ctx.rotate(ss.angle);

      // Trail gradient
      const trailGradient = ctx.createLinearGradient(-ss.length, 0, 0, 0);
      trailGradient.addColorStop(0, 'transparent');
      trailGradient.addColorStop(0.3, `rgba(255, 255, 255, ${lifeRatio * 0.3})`);
      trailGradient.addColorStop(1, `rgba(255, 255, 255, ${lifeRatio})`);

      ctx.strokeStyle = trailGradient;
      ctx.lineWidth = lineWidth;
      ctx.beginPath();
      ctx.moveTo(-ss.length, 0);
      ctx.lineTo(0, 0);
      ctx.stroke();

      // Big stars get a wider, softer outer glow trail
      if (ss.isBig) {
        const outerTrail = ctx.createLinearGradient(-ss.length * 0.7, 0, 0, 0);
        outerTrail.addColorStop(0, 'transparent');
        outerTrail.addColorStop(0.5, `rgba(200, 220, 255, ${lifeRatio * 0.15})`);
        outerTrail.addColorStop(1, `rgba(200, 220, 255, ${lifeRatio * 0.25})`);
        ctx.strokeStyle = outerTrail;
        ctx.lineWidth = 10;
        ctx.beginPath();
        ctx.moveTo(-ss.length * 0.7, 0);
        ctx.lineTo(0, 0);
        ctx.stroke();
      }

      // Head glow
      const headGlow = ctx.createRadialGradient(0, 0, 0, 0, 0, headRadius);
      headGlow.addColorStop(0, `rgba(255, 255, 255, ${lifeRatio})`);
      headGlow.addColorStop(0.4, `rgba(220, 240, 255, ${lifeRatio * 0.7})`);
      headGlow.addColorStop(1, 'transparent');
      ctx.fillStyle = headGlow;
      ctx.beginPath();
      ctx.arc(0, 0, headRadius, 0, Math.PI * 2);
      ctx.fill();

      // Big stars get an extra outer halo
      if (ss.isBig) {
        const halo = ctx.createRadialGradient(0, 0, headRadius * 0.5, 0, 0, headRadius * 2.5);
        halo.addColorStop(0, `rgba(200, 220, 255, ${lifeRatio * 0.3})`);
        halo.addColorStop(1, 'transparent');
        ctx.fillStyle = halo;
        ctx.beginPath();
        ctx.arc(0, 0, headRadius * 2.5, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.restore();
    };

    const drawCloud = (ctx: CanvasRenderingContext2D, cloud: Cloud) => {
      ctx.fillStyle = `rgba(100, 120, 150, ${cloud.opacity})`;

      // Draw wispy cloud shapes using pre-generated puffs
      cloud.puffs.forEach(puff => {
        const puffX = cloud.x + puff.offsetX;
        const puffY = cloud.y + puff.offsetY;

        ctx.beginPath();
        ctx.arc(puffX, puffY, puff.size, 0, Math.PI * 2);
        ctx.fill();
      });
    };

    const drawTree = (ctx: CanvasRenderingContext2D, tree: Tree, height: number) => {
      const baseY = height;

      ctx.fillStyle = '#0a0a0a';

      if (tree.type === 'pine') {
        // Pine tree silhouette
        ctx.beginPath();
        ctx.moveTo(tree.x, baseY);
        ctx.lineTo(tree.x - tree.width * 0.1, baseY);
        ctx.lineTo(tree.x - tree.width * 0.1, baseY - tree.height * 0.2);

        // Tree layers
        for (let layer = 0; layer < 4; layer++) {
          const layerY = baseY - tree.height * 0.2 - layer * tree.height * 0.2;
          const layerWidth = tree.width * (0.5 - layer * 0.1);
          ctx.lineTo(tree.x - layerWidth, layerY);
          ctx.lineTo(tree.x, layerY - tree.height * 0.15);
          ctx.lineTo(tree.x + layerWidth, layerY);
        }

        ctx.lineTo(tree.x + tree.width * 0.1, baseY - tree.height * 0.2);
        ctx.lineTo(tree.x + tree.width * 0.1, baseY);
        ctx.closePath();
        ctx.fill();
      } else {
        // Deciduous tree silhouette
        ctx.beginPath();
        ctx.moveTo(tree.x - tree.width * 0.1, baseY);
        ctx.lineTo(tree.x - tree.width * 0.1, baseY - tree.height * 0.4);

        // Canopy
        ctx.arc(tree.x, baseY - tree.height * 0.7, tree.width * 0.5, Math.PI, 0);
        ctx.lineTo(tree.x + tree.width * 0.1, baseY - tree.height * 0.4);
        ctx.lineTo(tree.x + tree.width * 0.1, baseY);
        ctx.closePath();
        ctx.fill();
      }
    };

    const drawFirefly = (ctx: CanvasRenderingContext2D, ff: Firefly) => {
      const glow = Math.sin(timeRef * 0.03 + ff.glowPhase) * 0.5 + 0.5;

      if (glow > 0.4) { // Higher threshold = less time visible
        // Smaller, dimmer outer glow
        const glowGradient = ctx.createRadialGradient(
          ff.x, ff.y, 0,
          ff.x, ff.y, ff.size * 4
        );
        glowGradient.addColorStop(0, `rgba(255, 255, 150, ${glow * 0.35})`);
        glowGradient.addColorStop(0.4, `rgba(255, 255, 100, ${glow * 0.15})`);
        glowGradient.addColorStop(1, 'transparent');

        ctx.fillStyle = glowGradient;
        ctx.beginPath();
        ctx.arc(ff.x, ff.y, ff.size * 4, 0, Math.PI * 2);
        ctx.fill();

        // Dimmer core
        ctx.fillStyle = `rgba(255, 255, 200, ${glow * 0.5})`;
        ctx.beginPath();
        ctx.arc(ff.x, ff.y, ff.size, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const drawGround = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
      // Ground silhouette with hills
      ctx.fillStyle = '#0a0a0a';
      ctx.beginPath();
      ctx.moveTo(0, height);

      for (let x = 0; x <= width; x += 20) {
        const hillY = height - 30 - Math.sin(x * 0.008) * 20 - Math.sin(x * 0.02) * 10;
        ctx.lineTo(x, hillY);
      }

      ctx.lineTo(width, height);
      ctx.closePath();
      ctx.fill();
    };

    const updateFirefly = (ff: Firefly, width: number, height: number) => {
      const dx = ff.targetX - ff.x;
      const dy = ff.targetY - ff.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist < 10) {
        ff.targetX = getRandomSidePosition(width);
        ff.targetY = height * 0.85 + Math.random() * height * 0.1; // Stay at tree level
      } else {
        ff.x += (dx / dist) * 0.15; // Slower movement
        ff.y += (dy / dist) * 0.15 + Math.sin(timeRef * 0.015 + ff.glowPhase) * 0.08;
      }
    };

    const animate = () => {
      const { width, height } = canvas;
      timeRef++;

      ctx.clearRect(0, 0, width, height);

      // Draw sky
      drawSky(ctx, width, height);

      // Update and draw clouds with smooth opacity transitions
      clouds.forEach(cloud => {
        cloud.x += cloud.speed;
        if (cloud.x > width + cloud.baseSize) {
          cloud.x = -cloud.baseSize;
          // Set new target opacity for graceful transition
          cloud.targetOpacity = 0.05 + Math.random() * 0.08;
        }
        // Smooth opacity interpolation
        cloud.opacity += (cloud.targetOpacity - cloud.opacity) * 0.005;
        drawCloud(ctx, cloud);
      });

      // Draw stars
      stars.forEach(star => drawStar(ctx, star));

      // Draw constellations
      constellations.forEach(constellation => drawConstellation(ctx, constellation));

      // Add shooting stars occasionally (less frequent)
      if (Math.random() < 0.001) {
        const startX = Math.random() * width;
        const startY = Math.random() * height * 0.4;
        shootingStars.push({
          x: startX,
          y: startY,
          angle: Math.PI * 0.15 + Math.random() * Math.PI * 0.2,
          speed: 6 + Math.random() * 6,
          length: 50 + Math.random() * 100,
          life: 60,
          maxLife: 60,
        });
      }

      // Very rarely spawn a big shooting star (about 10x rarer)
      if (Math.random() < 0.0001) {
        const startX = Math.random() * width;
        const startY = Math.random() * height * 0.3;
        shootingStars.push({
          x: startX,
          y: startY,
          angle: Math.PI * 0.15 + Math.random() * Math.PI * 0.15,
          speed: 8 + Math.random() * 6, // Faster
          length: 150 + Math.random() * 100, // Much longer trail
          life: 70,
          maxLife: 70, // Lasts longer
          isBig: true,
        });
      }

      // Update and draw shooting stars
      shootingStars = shootingStars.filter(ss => {
        ss.x += Math.cos(ss.angle) * ss.speed;
        ss.y += Math.sin(ss.angle) * ss.speed;
        ss.life--;
        drawShootingStar(ctx, ss);
        return ss.life > 0;
      });

      // Draw ground
      drawGround(ctx, width, height);

      // Draw trees
      trees.forEach(tree => drawTree(ctx, tree, height));

      // Update and draw fireflies
      fireflies.forEach(ff => {
        updateFirefly(ff, width, height);
        drawFirefly(ctx, ff);
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
