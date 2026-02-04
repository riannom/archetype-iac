/**
 * Bunny Meadow Animation
 *
 * Cute fluffy bunnies hopping around a meadow, eating grass,
 * and twitching their noses. Positioned on sides of screen.
 */

import { useEffect, RefObject } from 'react';

interface Bunny {
  x: number;
  y: number;
  size: number;
  color: 'white' | 'brown' | 'gray' | 'spotted';
  direction: 1 | -1;
  state: 'sitting' | 'hopping' | 'eating' | 'alert';
  stateTimer: number;
  hopPhase: number;
  hopHeight: number;
  earPhase: number;
  noseWiggle: number;
  targetX: number;
  blinkTimer: number;
  isBlinking: boolean;
  tailWiggle: number;
}

interface Flower {
  x: number;
  y: number;
  size: number;
  color: string;
  petalCount: number;
  swayPhase: number;
}

interface Grass {
  x: number;
  y: number;
  height: number;
  blades: number;
  swayPhase: number;
}

interface Butterfly {
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  wingPhase: number;
  color: string;
  size: number;
}

interface Cloud {
  x: number;
  y: number;
  size: number;
  speed: number;
  puffs: { x: number; y: number; size: number }[];
}

export function useBunnyMeadow(
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
    let bunnies: Bunny[] = [];
    let flowers: Flower[] = [];
    let grassPatches: Grass[] = [];
    let butterflies: Butterfly[] = [];
    let clouds: Cloud[] = [];
    let timeRef = 0;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeScene();
    };

    const getRandomSidePosition = (width: number): number => {
      if (Math.random() < 0.75) {
        return Math.random() < 0.5
          ? Math.random() * width * 0.25
          : width * 0.75 + Math.random() * width * 0.25;
      }
      return Math.random() * width;
    };

    const initializeScene = () => {
      const { width, height } = canvas;

      // Create bunnies
      bunnies = [];
      const bunnyCount = Math.ceil(width / 350);
      const colors: Bunny['color'][] = ['white', 'brown', 'gray', 'spotted'];

      for (let i = 0; i < bunnyCount; i++) {
        bunnies.push({
          x: getRandomSidePosition(width),
          y: height * 0.55 + Math.random() * height * 0.35,
          size: 30 + Math.random() * 15,
          color: colors[Math.floor(Math.random() * colors.length)],
          direction: Math.random() < 0.5 ? 1 : -1,
          state: 'sitting',
          stateTimer: 100 + Math.random() * 200,
          hopPhase: 0,
          hopHeight: 0,
          earPhase: Math.random() * Math.PI * 2,
          noseWiggle: Math.random() * Math.PI * 2,
          targetX: getRandomSidePosition(width),
          blinkTimer: Math.random() * 200,
          isBlinking: false,
          tailWiggle: 0,
        });
      }

      // Create flowers
      flowers = [];
      const flowerCount = Math.floor(width / 80);
      const flowerColors = ['#FFB6C1', '#FF69B4', '#DDA0DD', '#FFD700', '#FFA500', '#87CEEB', '#FFFFFF'];

      for (let i = 0; i < flowerCount; i++) {
        flowers.push({
          x: getRandomSidePosition(width),
          y: height * 0.5 + Math.random() * height * 0.45,
          size: 8 + Math.random() * 12,
          color: flowerColors[Math.floor(Math.random() * flowerColors.length)],
          petalCount: 5 + Math.floor(Math.random() * 3),
          swayPhase: Math.random() * Math.PI * 2,
        });
      }

      // Create grass patches
      grassPatches = [];
      const grassCount = Math.floor(width / 60);
      for (let i = 0; i < grassCount; i++) {
        grassPatches.push({
          x: getRandomSidePosition(width),
          y: height * 0.5 + Math.random() * height * 0.45,
          height: 15 + Math.random() * 25,
          blades: 4 + Math.floor(Math.random() * 4),
          swayPhase: Math.random() * Math.PI * 2,
        });
      }

      // Create butterflies
      butterflies = [];
      for (let i = 0; i < 4; i++) {
        butterflies.push({
          x: Math.random() * width,
          y: height * 0.2 + Math.random() * height * 0.4,
          targetX: Math.random() * width,
          targetY: height * 0.2 + Math.random() * height * 0.4,
          wingPhase: Math.random() * Math.PI * 2,
          color: flowerColors[Math.floor(Math.random() * flowerColors.length)],
          size: 6 + Math.random() * 6,
        });
      }

      // Create clouds
      clouds = [];
      const cloudCount = 4;
      for (let i = 0; i < cloudCount; i++) {
        const puffs = [];
        const puffCount = 3 + Math.floor(Math.random() * 3);
        for (let p = 0; p < puffCount; p++) {
          puffs.push({
            x: (p - puffCount / 2) * 25,
            y: (Math.random() - 0.5) * 15,
            size: 20 + Math.random() * 20,
          });
        }
        clouds.push({
          x: (i / cloudCount) * width + Math.random() * 100,
          y: height * 0.05 + Math.random() * height * 0.15,
          size: 1,
          speed: 0.1 + Math.random() * 0.15,
          puffs,
        });
      }
    };

    const drawBackground = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
      // Sky gradient
      const skyGradient = ctx.createLinearGradient(0, 0, 0, height * 0.5);
      if (darkMode) {
        skyGradient.addColorStop(0, '#0a1a2a');
        skyGradient.addColorStop(1, '#1a3a4a');
      } else {
        skyGradient.addColorStop(0, '#87CEEB');
        skyGradient.addColorStop(1, '#E0F6FF');
      }
      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, width, height);

      // Meadow gradient
      const meadowGradient = ctx.createLinearGradient(0, height * 0.4, 0, height);
      if (darkMode) {
        meadowGradient.addColorStop(0, '#1a3a2a');
        meadowGradient.addColorStop(0.5, '#2a4a3a');
        meadowGradient.addColorStop(1, '#1a3020');
      } else {
        meadowGradient.addColorStop(0, '#90EE90');
        meadowGradient.addColorStop(0.3, '#7CCD7C');
        meadowGradient.addColorStop(1, '#228B22');
      }
      ctx.fillStyle = meadowGradient;
      ctx.fillRect(0, height * 0.4, width, height * 0.6);

      // Rolling hills
      ctx.fillStyle = darkMode ? '#2a4a3a' : '#8FBC8F';
      for (let i = 0; i < 3; i++) {
        ctx.beginPath();
        ctx.moveTo(0, height * 0.5 + i * 30);
        for (let x = 0; x <= width; x += 50) {
          const hillY = height * 0.5 + i * 30 + Math.sin(x * 0.005 + i) * 20;
          ctx.lineTo(x, hillY);
        }
        ctx.lineTo(width, height);
        ctx.lineTo(0, height);
        ctx.closePath();
        ctx.fill();
      }
    };

    const drawCloud = (ctx: CanvasRenderingContext2D, cloud: Cloud) => {
      ctx.save();
      ctx.translate(cloud.x, cloud.y);

      ctx.fillStyle = darkMode ? 'rgba(60, 80, 100, 0.5)' : 'rgba(255, 255, 255, 0.9)';
      cloud.puffs.forEach(puff => {
        ctx.beginPath();
        ctx.arc(puff.x, puff.y, puff.size, 0, Math.PI * 2);
        ctx.fill();
      });

      ctx.restore();
    };

    const drawGrass = (ctx: CanvasRenderingContext2D, grass: Grass) => {
      const sway = Math.sin(timeRef * 0.002 + grass.swayPhase) * 3;

      ctx.save();
      ctx.translate(grass.x, grass.y);

      for (let i = 0; i < grass.blades; i++) {
        const bladeX = (i - grass.blades / 2) * 4;
        const bladeHeight = grass.height * (0.7 + Math.random() * 0.3);
        const bladeSway = sway * (0.8 + i * 0.1);

        ctx.strokeStyle = darkMode ? '#3a5a3a' : '#228B22';
        ctx.lineWidth = 2;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(bladeX, 0);
        ctx.quadraticCurveTo(
          bladeX + bladeSway * 0.5,
          -bladeHeight * 0.5,
          bladeX + bladeSway,
          -bladeHeight
        );
        ctx.stroke();
      }

      ctx.restore();
    };

    const drawFlower = (ctx: CanvasRenderingContext2D, flower: Flower) => {
      const sway = Math.sin(timeRef * 0.002 + flower.swayPhase) * 2;

      ctx.save();
      ctx.translate(flower.x + sway, flower.y);

      // Stem
      ctx.strokeStyle = darkMode ? '#2a4a2a' : '#228B22';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.quadraticCurveTo(sway * 0.5, -flower.size, sway, -flower.size * 2);
      ctx.stroke();

      // Petals
      ctx.save();
      ctx.translate(sway, -flower.size * 2);

      for (let i = 0; i < flower.petalCount; i++) {
        const angle = (i / flower.petalCount) * Math.PI * 2;
        ctx.save();
        ctx.rotate(angle);

        const petalGradient = ctx.createRadialGradient(
          flower.size * 0.4, 0, 0,
          flower.size * 0.4, 0, flower.size * 0.5
        );
        petalGradient.addColorStop(0, '#FFFFFF');
        petalGradient.addColorStop(0.5, flower.color);
        petalGradient.addColorStop(1, flower.color);

        ctx.fillStyle = petalGradient;
        ctx.beginPath();
        ctx.ellipse(flower.size * 0.4, 0, flower.size * 0.4, flower.size * 0.2, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      // Center
      ctx.fillStyle = '#FFD700';
      ctx.beginPath();
      ctx.arc(0, 0, flower.size * 0.2, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
      ctx.restore();
    };

    const drawButterfly = (ctx: CanvasRenderingContext2D, bf: Butterfly) => {
      const wingAngle = Math.sin(timeRef * 0.1 + bf.wingPhase) * 0.5;

      ctx.save();
      ctx.translate(bf.x, bf.y);

      const dx = bf.targetX - bf.x;
      ctx.scale(dx > 0 ? 1 : -1, 1);

      // Wings
      ctx.fillStyle = bf.color;
      ctx.globalAlpha = 0.7;

      ctx.save();
      ctx.rotate(wingAngle);
      ctx.beginPath();
      ctx.ellipse(-bf.size * 0.3, -bf.size * 0.5, bf.size * 0.4, bf.size * 0.5, -0.3, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      ctx.save();
      ctx.rotate(-wingAngle);
      ctx.beginPath();
      ctx.ellipse(-bf.size * 0.3, bf.size * 0.5, bf.size * 0.4, bf.size * 0.5, 0.3, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      ctx.globalAlpha = 1;

      // Body
      ctx.fillStyle = '#333';
      ctx.beginPath();
      ctx.ellipse(0, 0, bf.size * 0.08, bf.size * 0.25, 0, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
    };

    const drawBunny = (ctx: CanvasRenderingContext2D, bunny: Bunny) => {
      const size = bunny.size;
      const hopOffset = bunny.hopHeight;
      const earWave = Math.sin(timeRef * 0.02 + bunny.earPhase) * 3;
      const noseWiggle = Math.sin(timeRef * 0.15 + bunny.noseWiggle) * 2;

      ctx.save();
      ctx.translate(bunny.x, bunny.y - hopOffset);
      ctx.scale(bunny.direction, 1);

      // Get colors based on bunny type
      let mainColor: string, shadowColor: string, highlightColor: string;
      switch (bunny.color) {
        case 'white':
          mainColor = darkMode ? '#D0D0D0' : '#FFFAFA';
          shadowColor = darkMode ? '#A0A0A0' : '#E0E0E0';
          highlightColor = darkMode ? '#E8E8E8' : '#FFFFFF';
          break;
        case 'brown':
          mainColor = darkMode ? '#6B4423' : '#8B6914';
          shadowColor = darkMode ? '#4B2413' : '#6B4904';
          highlightColor = darkMode ? '#8B5433' : '#9B7924';
          break;
        case 'gray':
          mainColor = darkMode ? '#606060' : '#808080';
          shadowColor = darkMode ? '#404040' : '#606060';
          highlightColor = darkMode ? '#808080' : '#A0A0A0';
          break;
        case 'spotted':
          mainColor = darkMode ? '#C8C8C8' : '#FFFAFA';
          shadowColor = darkMode ? '#888888' : '#D0D0D0';
          highlightColor = darkMode ? '#E0E0E0' : '#FFFFFF';
          break;
      }

      // Shadow
      ctx.fillStyle = 'rgba(0, 0, 0, 0.15)';
      ctx.beginPath();
      ctx.ellipse(0, hopOffset + size * 0.5, size * 0.5, size * 0.15, 0, 0, Math.PI * 2);
      ctx.fill();

      // Back leg (when hopping)
      if (bunny.state === 'hopping') {
        ctx.fillStyle = shadowColor;
        ctx.beginPath();
        ctx.ellipse(-size * 0.15, size * 0.35, size * 0.2, size * 0.12, -0.5, 0, Math.PI * 2);
        ctx.fill();
      }

      // Tail (fluffy puffball)
      const tailWiggle = Math.sin(bunny.tailWiggle) * 3;
      ctx.fillStyle = highlightColor;
      ctx.beginPath();
      ctx.arc(-size * 0.35 + tailWiggle, size * 0.1, size * 0.15, 0, Math.PI * 2);
      ctx.fill();
      // Tail fluff
      ctx.fillStyle = mainColor;
      ctx.beginPath();
      ctx.arc(-size * 0.32 + tailWiggle, size * 0.08, size * 0.1, 0, Math.PI * 2);
      ctx.fill();

      // Body
      const bodyGradient = ctx.createRadialGradient(
        size * 0.1, -size * 0.1, 0,
        0, 0, size * 0.5
      );
      bodyGradient.addColorStop(0, highlightColor);
      bodyGradient.addColorStop(0.7, mainColor);
      bodyGradient.addColorStop(1, shadowColor);

      ctx.fillStyle = bodyGradient;
      ctx.beginPath();
      ctx.ellipse(0, size * 0.1, size * 0.4, size * 0.35, 0, 0, Math.PI * 2);
      ctx.fill();

      // Spots for spotted bunny
      if (bunny.color === 'spotted') {
        ctx.fillStyle = darkMode ? '#4a3a2a' : '#8B6914';
        ctx.beginPath();
        ctx.ellipse(-size * 0.15, size * 0.05, size * 0.1, size * 0.08, 0.3, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.ellipse(size * 0.1, size * 0.2, size * 0.08, size * 0.06, -0.2, 0, Math.PI * 2);
        ctx.fill();
      }

      // Front paws
      ctx.fillStyle = mainColor;
      if (bunny.state === 'eating') {
        // Paws up
        ctx.beginPath();
        ctx.ellipse(size * 0.25, -size * 0.15, size * 0.08, size * 0.12, 0.3, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.ellipse(size * 0.35, -size * 0.1, size * 0.08, size * 0.12, 0.5, 0, Math.PI * 2);
        ctx.fill();
      } else {
        // Paws down
        ctx.beginPath();
        ctx.ellipse(size * 0.2, size * 0.35, size * 0.1, size * 0.06, 0, 0, Math.PI * 2);
        ctx.fill();
      }

      // Head
      const headGradient = ctx.createRadialGradient(
        size * 0.35, -size * 0.35, 0,
        size * 0.3, -size * 0.25, size * 0.35
      );
      headGradient.addColorStop(0, highlightColor);
      headGradient.addColorStop(0.6, mainColor);
      headGradient.addColorStop(1, shadowColor);

      ctx.fillStyle = headGradient;
      ctx.beginPath();
      ctx.arc(size * 0.3, -size * 0.2, size * 0.28, 0, Math.PI * 2);
      ctx.fill();

      // Cheeks
      ctx.fillStyle = 'rgba(255, 182, 193, 0.3)';
      ctx.beginPath();
      ctx.ellipse(size * 0.45, -size * 0.1, size * 0.1, size * 0.08, 0, 0, Math.PI * 2);
      ctx.fill();

      // Ears
      const earAngle = bunny.state === 'alert' ? -0.2 : 0.1;

      // Left ear
      ctx.save();
      ctx.translate(size * 0.2, -size * 0.4);
      ctx.rotate(earAngle + earWave * 0.02);

      // Outer ear
      ctx.fillStyle = mainColor;
      ctx.beginPath();
      ctx.ellipse(0, -size * 0.35, size * 0.12, size * 0.35, 0, 0, Math.PI * 2);
      ctx.fill();
      // Inner ear
      ctx.fillStyle = darkMode ? '#8a6a5a' : '#FFB6C1';
      ctx.beginPath();
      ctx.ellipse(0, -size * 0.3, size * 0.06, size * 0.25, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // Right ear
      ctx.save();
      ctx.translate(size * 0.35, -size * 0.42);
      ctx.rotate(earAngle - 0.15 - earWave * 0.01);

      ctx.fillStyle = mainColor;
      ctx.beginPath();
      ctx.ellipse(0, -size * 0.35, size * 0.12, size * 0.35, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = darkMode ? '#8a6a5a' : '#FFB6C1';
      ctx.beginPath();
      ctx.ellipse(0, -size * 0.3, size * 0.06, size * 0.25, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // Snout
      ctx.fillStyle = highlightColor;
      ctx.beginPath();
      ctx.ellipse(size * 0.48, -size * 0.12, size * 0.12, size * 0.1, 0, 0, Math.PI * 2);
      ctx.fill();

      // Nose
      ctx.fillStyle = darkMode ? '#8a5a4a' : '#FFB6C1';
      ctx.beginPath();
      ctx.ellipse(size * 0.55 + noseWiggle * 0.3, -size * 0.18, size * 0.05, size * 0.04, 0, 0, Math.PI * 2);
      ctx.fill();

      // Mouth (Y shape)
      ctx.strokeStyle = darkMode ? '#5a4a4a' : '#8B6969';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(size * 0.55, -size * 0.14);
      ctx.lineTo(size * 0.55, -size * 0.08);
      ctx.moveTo(size * 0.52, -size * 0.05);
      ctx.lineTo(size * 0.55, -size * 0.08);
      ctx.lineTo(size * 0.58, -size * 0.05);
      ctx.stroke();

      // Eyes
      if (!bunny.isBlinking) {
        // Eye white
        ctx.fillStyle = '#FFFFFF';
        ctx.beginPath();
        ctx.ellipse(size * 0.38, -size * 0.28, size * 0.08, size * 0.1, 0, 0, Math.PI * 2);
        ctx.fill();

        // Pupil
        ctx.fillStyle = '#000000';
        ctx.beginPath();
        ctx.arc(size * 0.4, -size * 0.27, size * 0.045, 0, Math.PI * 2);
        ctx.fill();

        // Highlight
        ctx.fillStyle = '#FFFFFF';
        ctx.beginPath();
        ctx.arc(size * 0.42, -size * 0.29, size * 0.015, 0, Math.PI * 2);
        ctx.fill();
      } else {
        // Closed eye (happy arc)
        ctx.strokeStyle = '#000000';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(size * 0.38, -size * 0.27, size * 0.05, 0.3, Math.PI - 0.3);
        ctx.stroke();
      }

      ctx.restore();
    };

    const updateBunny = (bunny: Bunny, width: number) => {
      bunny.stateTimer--;
      bunny.noseWiggle += 0.1;

      // Update blinking
      bunny.blinkTimer--;
      if (bunny.blinkTimer <= 0) {
        bunny.isBlinking = !bunny.isBlinking;
        bunny.blinkTimer = bunny.isBlinking ? 10 : (120 + Math.random() * 180);
      }

      switch (bunny.state) {
        case 'sitting':
          bunny.hopHeight = 0;
          if (bunny.stateTimer <= 0) {
            const states: Bunny['state'][] = ['hopping', 'eating', 'alert', 'sitting'];
            bunny.state = states[Math.floor(Math.random() * states.length)];
            bunny.stateTimer = 80 + Math.random() * 150;

            if (bunny.state === 'hopping') {
              bunny.targetX = getRandomSidePosition(width);
              bunny.direction = bunny.targetX > bunny.x ? 1 : -1;
            }
          }
          break;

        case 'hopping':
          bunny.hopPhase += 0.15;
          bunny.hopHeight = Math.abs(Math.sin(bunny.hopPhase)) * 30;

          // Move when at peak of hop
          if (Math.sin(bunny.hopPhase) > 0.5) {
            bunny.x += bunny.direction * 2;
            bunny.tailWiggle += 0.3;
          }

          // Check if reached target
          if (Math.abs(bunny.x - bunny.targetX) < 20 || bunny.stateTimer <= 0) {
            bunny.state = 'sitting';
            bunny.stateTimer = 100 + Math.random() * 200;
            bunny.hopHeight = 0;
          }
          break;

        case 'eating':
          bunny.noseWiggle += 0.1;
          if (bunny.stateTimer <= 0) {
            bunny.state = 'sitting';
            bunny.stateTimer = 80 + Math.random() * 150;
          }
          break;

        case 'alert':
          bunny.earPhase += 0.05;
          if (bunny.stateTimer <= 0) {
            bunny.state = Math.random() < 0.5 ? 'hopping' : 'sitting';
            bunny.stateTimer = 100 + Math.random() * 150;
            if (bunny.state === 'hopping') {
              bunny.targetX = getRandomSidePosition(width);
              bunny.direction = bunny.targetX > bunny.x ? 1 : -1;
            }
          }
          break;
      }
    };

    const updateButterfly = (bf: Butterfly, width: number, height: number) => {
      const dx = bf.targetX - bf.x;
      const dy = bf.targetY - bf.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist < 15) {
        bf.targetX = Math.random() * width;
        bf.targetY = height * 0.2 + Math.random() * height * 0.4;
      } else {
        bf.x += (dx / dist) * 1.2;
        bf.y += (dy / dist) * 1.2 + Math.sin(timeRef * 0.05) * 0.5;
      }
    };

    const animate = () => {
      const { width, height } = canvas;
      timeRef++;

      ctx.clearRect(0, 0, width, height);

      // Draw background
      drawBackground(ctx, width, height);

      // Update and draw clouds
      clouds.forEach(cloud => {
        cloud.x += cloud.speed;
        if (cloud.x > width + 100) {
          cloud.x = -100;
        }
        drawCloud(ctx, cloud);
      });

      // Draw grass
      grassPatches.forEach(grass => drawGrass(ctx, grass));

      // Draw flowers
      flowers.forEach(flower => drawFlower(ctx, flower));

      // Update and draw butterflies
      butterflies.forEach(bf => {
        updateButterfly(bf, width, height);
        drawButterfly(ctx, bf);
      });

      // Update and draw bunnies
      bunnies.forEach(bunny => {
        updateBunny(bunny, width);
        drawBunny(ctx, bunny);
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
