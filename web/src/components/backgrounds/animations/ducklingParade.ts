/**
 * Duckling Parade Animation
 *
 * Adorable mother duck with ducklings following in a line,
 * waddling along paths near water. Positioned on sides of screen.
 */

import { useEffect, RefObject } from 'react';

interface Duck {
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  size: number;
  waddle: number;
  isMother: boolean;
  followIndex: number;
  bobPhase: number;
  blinkTimer: number;
  isBlinking: boolean;
  quackTimer: number;
  isQuacking: boolean;
  wingFlap: number;
}

interface DuckFamily {
  ducks: Duck[];
  direction: 1 | -1;
  pathY: number;
  leader: Duck;
}

interface Ripple {
  x: number;
  y: number;
  radius: number;
  maxRadius: number;
  opacity: number;
}

interface GrassBlade {
  heightMult: number;
  swayMult: number;
}

interface Grass {
  x: number;
  y: number;
  height: number;
  blades: number;
  swayPhase: number;
  bladeData: GrassBlade[];
}

interface Butterfly {
  x: number;
  y: number;
  vx: number;
  vy: number;
  wingPhase: number;
  color: string;
  size: number;
  // Target position for flight direction calculation
  targetX: number;
  targetY: number;
  // Erratic flight parameters
  flutterPhase: number;
  flutterSpeed: number;
  turnTimer: number;
  turnInterval: number;
  hoverTimer: number;
  isHovering: boolean;
  baseSpeed: number;
}

export function useDucklingParade(
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
    let families: DuckFamily[] = [];
    let ripples: Ripple[] = [];
    let grassPatches: Grass[] = [];
    let butterflies: Butterfly[] = [];
    let timeRef = 0;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeScene();
    };

    const getRandomSideY = (height: number): number => {
      // Prefer top and bottom areas
      if (Math.random() < 0.7) {
        return Math.random() < 0.5
          ? height * 0.1 + Math.random() * height * 0.2
          : height * 0.7 + Math.random() * height * 0.2;
      }
      return height * 0.3 + Math.random() * height * 0.4;
    };

    const initializeScene = () => {
      const { width, height } = canvas;

      // Create duck families
      families = [];
      const familyCount = Math.ceil(width / 600);

      for (let f = 0; f < familyCount; f++) {
        const pathY = getRandomSideY(height);
        const direction = Math.random() < 0.5 ? 1 : -1;
        const startX = direction === 1 ? -100 - f * 200 : width + 100 + f * 200;
        const ducklingCount = 3 + Math.floor(Math.random() * 4);

        const ducks: Duck[] = [];

        // Mother duck
        const mother: Duck = {
          x: startX,
          y: pathY,
          targetX: startX + direction * 100,
          targetY: pathY,
          size: 40,
          waddle: 0,
          isMother: true,
          followIndex: -1,
          bobPhase: Math.random() * Math.PI * 2,
          blinkTimer: Math.random() * 200,
          isBlinking: false,
          quackTimer: Math.random() * 300,
          isQuacking: false,
          wingFlap: 0,
        };
        ducks.push(mother);

        // Ducklings
        for (let i = 0; i < ducklingCount; i++) {
          ducks.push({
            x: startX - direction * (30 + i * 25),
            y: pathY + (Math.random() - 0.5) * 10,
            targetX: mother.x - direction * 30,
            targetY: pathY,
            size: 18 + Math.random() * 6,
            waddle: Math.random() * Math.PI * 2,
            isMother: false,
            followIndex: i,
            bobPhase: Math.random() * Math.PI * 2,
            blinkTimer: Math.random() * 200,
            isBlinking: false,
            quackTimer: Math.random() * 500,
            isQuacking: false,
            wingFlap: 0,
          });
        }

        families.push({
          ducks,
          direction,
          pathY,
          leader: mother,
        });
      }

      // Create grass patches on edges with pre-generated blade data
      grassPatches = [];
      const grassCount = Math.floor(width / 80);
      for (let i = 0; i < grassCount; i++) {
        const side = Math.random() < 0.5 ? 0 : 1;
        const bladeCount = 5 + Math.floor(Math.random() * 5);
        const bladeData: GrassBlade[] = [];
        for (let b = 0; b < bladeCount; b++) {
          bladeData.push({
            heightMult: 0.7 + Math.random() * 0.3,
            swayMult: 0.8 + Math.random() * 0.4,
          });
        }
        grassPatches.push({
          x: side === 0 ? Math.random() * width * 0.2 : width * 0.8 + Math.random() * width * 0.2,
          y: height * 0.3 + Math.random() * height * 0.6,
          height: 20 + Math.random() * 30,
          blades: bladeCount,
          swayPhase: Math.random() * Math.PI * 2,
          bladeData,
        });
      }

      // Create butterflies
      butterflies = [];
      for (let i = 0; i < 3; i++) {
        const angle = Math.random() * Math.PI * 2;
        const baseSpeed = 0.8 + Math.random() * 0.6;
        const startX = Math.random() * width;
        const startY = Math.random() * height * 0.5;
        butterflies.push({
          x: startX,
          y: startY,
          vx: Math.cos(angle) * baseSpeed,
          vy: Math.sin(angle) * baseSpeed,
          wingPhase: Math.random() * Math.PI * 2,
          color: ['#FFB6C1', '#87CEEB', '#DDA0DD', '#F0E68C'][Math.floor(Math.random() * 4)],
          size: 8 + Math.random() * 6,
          targetX: startX + Math.cos(angle) * 100,
          targetY: startY + Math.sin(angle) * 100,
          flutterPhase: Math.random() * Math.PI * 2,
          flutterSpeed: 0.15 + Math.random() * 0.1,
          turnTimer: 0,
          turnInterval: 30 + Math.floor(Math.random() * 60),
          hoverTimer: 0,
          isHovering: false,
          baseSpeed,
        });
      }

      ripples = [];
    };

    const drawBackground = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
      // Grass/pond gradient
      const bgGradient = ctx.createLinearGradient(0, 0, 0, height);
      if (darkMode) {
        bgGradient.addColorStop(0, '#1a2a1a');
        bgGradient.addColorStop(0.4, '#1a3a2a');
        bgGradient.addColorStop(0.6, '#0d2a35');
        bgGradient.addColorStop(1, '#051520');
      } else {
        bgGradient.addColorStop(0, '#90EE90');
        bgGradient.addColorStop(0.3, '#7CCD7C');
        bgGradient.addColorStop(0.5, '#87CEEB');
        bgGradient.addColorStop(1, '#5BA3C0');
      }
      ctx.fillStyle = bgGradient;
      ctx.fillRect(0, 0, width, height);

      // Add some path/pond areas
      ctx.fillStyle = darkMode ? 'rgba(20, 60, 80, 0.3)' : 'rgba(135, 206, 235, 0.4)';
      for (const family of families) {
        ctx.beginPath();
        ctx.ellipse(width / 2, family.pathY, width * 0.4, 40, 0, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const drawGrass = (ctx: CanvasRenderingContext2D, grass: Grass) => {
      // Reduced sway for more graceful movement
      const sway = Math.sin(timeRef * 0.0008 + grass.swayPhase) * 2;

      ctx.save();
      ctx.translate(grass.x, grass.y);

      for (let i = 0; i < grass.blades; i++) {
        const blade = grass.bladeData[i];
        const bladeX = (i - grass.blades / 2) * 4;
        const bladeHeight = grass.height * blade.heightMult;
        const bladeSway = sway * blade.swayMult;

        const grassGradient = ctx.createLinearGradient(0, 0, 0, -bladeHeight);
        if (darkMode) {
          grassGradient.addColorStop(0, '#2a4a2a');
          grassGradient.addColorStop(1, '#3a6a3a');
        } else {
          grassGradient.addColorStop(0, '#228B22');
          grassGradient.addColorStop(1, '#32CD32');
        }

        ctx.strokeStyle = grassGradient;
        ctx.lineWidth = 3;
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

    const drawDuck = (ctx: CanvasRenderingContext2D, duck: Duck, direction: number) => {
      const waddle = Math.sin(timeRef * 0.08 + duck.waddle) * 5;
      const bob = Math.sin(timeRef * 0.05 + duck.bobPhase) * 2;
      const size = duck.size;

      ctx.save();
      ctx.translate(duck.x, duck.y + bob);
      ctx.scale(direction, 1);

      // Shadow
      ctx.fillStyle = 'rgba(0, 0, 0, 0.15)';
      ctx.beginPath();
      ctx.ellipse(0, size * 0.4, size * 0.6, size * 0.15, 0, 0, Math.PI * 2);
      ctx.fill();

      // Body colors
      const bodyColor = duck.isMother
        ? (darkMode ? '#6B5B4B' : '#8B7355')
        : (darkMode ? '#B8A800' : '#FFD700');
      const bodyHighlight = duck.isMother
        ? (darkMode ? '#8B7B6B' : '#A08060')
        : (darkMode ? '#D8C820' : '#FFEC8B');
      const bodyDark = duck.isMother
        ? (darkMode ? '#4B3B2B' : '#6B5340')
        : (darkMode ? '#988800' : '#DAA520');

      // Tail feathers
      ctx.fillStyle = bodyColor;
      ctx.beginPath();
      ctx.moveTo(-size * 0.3, 0);
      for (let i = 0; i < 3; i++) {
        const tailY = -size * 0.1 + i * size * 0.1;
        ctx.lineTo(-size * 0.6, tailY);
        ctx.lineTo(-size * 0.4, tailY + size * 0.05);
      }
      ctx.closePath();
      ctx.fill();

      // Body
      const bodyGradient = ctx.createRadialGradient(
        size * 0.1, -size * 0.1, 0,
        0, 0, size * 0.5
      );
      bodyGradient.addColorStop(0, bodyHighlight);
      bodyGradient.addColorStop(0.6, bodyColor);
      bodyGradient.addColorStop(1, bodyDark);

      ctx.fillStyle = bodyGradient;
      ctx.beginPath();
      ctx.ellipse(0, 0, size * 0.45, size * 0.35, waddle * 0.02, 0, Math.PI * 2);
      ctx.fill();

      // Wing
      ctx.fillStyle = bodyDark;
      const wingFlap = Math.sin(duck.wingFlap) * 5;
      ctx.beginPath();
      ctx.ellipse(-size * 0.1, -size * 0.05 + wingFlap, size * 0.25, size * 0.18, 0.3, 0, Math.PI * 2);
      ctx.fill();

      // Wing detail
      ctx.strokeStyle = duck.isMother ? '#5B4B3B' : '#C8B800';
      ctx.lineWidth = 1;
      for (let i = 0; i < 3; i++) {
        ctx.beginPath();
        ctx.arc(-size * 0.1, -size * 0.05 + wingFlap, size * 0.12 + i * 4, -0.5, 0.8);
        ctx.stroke();
      }

      // Neck
      ctx.fillStyle = bodyColor;
      ctx.beginPath();
      ctx.ellipse(size * 0.25, -size * 0.2, size * 0.15, size * 0.2, -0.3, 0, Math.PI * 2);
      ctx.fill();

      // Head
      const headGradient = ctx.createRadialGradient(
        size * 0.4, -size * 0.35, 0,
        size * 0.35, -size * 0.4, size * 0.25
      );
      if (duck.isMother) {
        headGradient.addColorStop(0, darkMode ? '#4B6B4B' : '#4A6B4A');
        headGradient.addColorStop(1, darkMode ? '#2B4B2B' : '#2A4B2A');
      } else {
        headGradient.addColorStop(0, darkMode ? '#D8C820' : '#FFEC8B');
        headGradient.addColorStop(1, darkMode ? '#B8A800' : '#FFD700');
      }

      ctx.fillStyle = headGradient;
      ctx.beginPath();
      ctx.arc(size * 0.35, -size * 0.4, size * 0.2, 0, Math.PI * 2);
      ctx.fill();

      // Cheek (for ducklings)
      if (!duck.isMother) {
        ctx.fillStyle = 'rgba(255, 180, 180, 0.3)';
        ctx.beginPath();
        ctx.arc(size * 0.45, -size * 0.35, size * 0.08, 0, Math.PI * 2);
        ctx.fill();
      }

      // Eye
      if (!duck.isBlinking) {
        // Eye white
        ctx.fillStyle = '#FFFFFF';
        ctx.beginPath();
        ctx.ellipse(size * 0.42, -size * 0.45, size * 0.08, size * 0.1, 0, 0, Math.PI * 2);
        ctx.fill();

        // Pupil
        ctx.fillStyle = '#000000';
        ctx.beginPath();
        ctx.arc(size * 0.44, -size * 0.44, size * 0.04, 0, Math.PI * 2);
        ctx.fill();

        // Eye highlight
        ctx.fillStyle = '#FFFFFF';
        ctx.beginPath();
        ctx.arc(size * 0.46, -size * 0.46, size * 0.015, 0, Math.PI * 2);
        ctx.fill();
      } else {
        // Closed eye
        ctx.strokeStyle = '#000000';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(size * 0.42, -size * 0.44, size * 0.05, 0.2, Math.PI - 0.2);
        ctx.stroke();
      }

      // Beak
      const beakOpen = duck.isQuacking ? size * 0.04 : 0;
      ctx.fillStyle = '#FF8C00';
      ctx.beginPath();
      ctx.moveTo(size * 0.5, -size * 0.4 - beakOpen);
      ctx.lineTo(size * 0.7, -size * 0.38);
      ctx.lineTo(size * 0.5, -size * 0.35 + beakOpen);
      ctx.closePath();
      ctx.fill();

      // Beak nostril
      ctx.fillStyle = '#D87000';
      ctx.beginPath();
      ctx.arc(size * 0.55, -size * 0.38, size * 0.015, 0, Math.PI * 2);
      ctx.fill();

      // Feet (if not in water)
      ctx.fillStyle = '#FF8C00';
      const footY = size * 0.35;
      // Left foot
      ctx.beginPath();
      ctx.moveTo(-size * 0.1, footY);
      ctx.lineTo(-size * 0.2, footY + size * 0.1);
      ctx.lineTo(-size * 0.1, footY + size * 0.05);
      ctx.lineTo(0, footY + size * 0.1);
      ctx.lineTo(-size * 0.05, footY);
      ctx.closePath();
      ctx.fill();
      // Right foot
      ctx.beginPath();
      ctx.moveTo(size * 0.1, footY);
      ctx.lineTo(0, footY + size * 0.1);
      ctx.lineTo(size * 0.1, footY + size * 0.05);
      ctx.lineTo(size * 0.2, footY + size * 0.1);
      ctx.lineTo(size * 0.15, footY);
      ctx.closePath();
      ctx.fill();

      ctx.restore();
    };

    const drawRipple = (ctx: CanvasRenderingContext2D, ripple: Ripple) => {
      ctx.strokeStyle = darkMode
        ? `rgba(100, 150, 180, ${ripple.opacity})`
        : `rgba(255, 255, 255, ${ripple.opacity})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(ripple.x, ripple.y, ripple.radius, 0, Math.PI * 2);
      ctx.stroke();
    };

    const drawButterfly = (ctx: CanvasRenderingContext2D, bf: Butterfly) => {
      const wingAngle = Math.sin(timeRef * 0.1 + bf.wingPhase) * 0.5;

      ctx.save();
      ctx.translate(bf.x, bf.y);

      // Calculate movement direction for rotation
      const dx = bf.targetX - bf.x;
      const angle = dx > 0 ? 0 : Math.PI;
      ctx.rotate(angle);

      // Wings
      ctx.fillStyle = bf.color;
      ctx.globalAlpha = 0.8;

      // Upper wings
      ctx.save();
      ctx.rotate(wingAngle);
      ctx.beginPath();
      ctx.ellipse(-bf.size * 0.3, -bf.size * 0.5, bf.size * 0.4, bf.size * 0.6, -0.3, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      ctx.save();
      ctx.rotate(-wingAngle);
      ctx.beginPath();
      ctx.ellipse(-bf.size * 0.3, bf.size * 0.5, bf.size * 0.4, bf.size * 0.6, 0.3, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // Lower wings (smaller)
      ctx.save();
      ctx.rotate(wingAngle * 0.8);
      ctx.beginPath();
      ctx.ellipse(-bf.size * 0.5, -bf.size * 0.25, bf.size * 0.25, bf.size * 0.35, -0.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      ctx.save();
      ctx.rotate(-wingAngle * 0.8);
      ctx.beginPath();
      ctx.ellipse(-bf.size * 0.5, bf.size * 0.25, bf.size * 0.25, bf.size * 0.35, 0.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      ctx.globalAlpha = 1;

      // Body
      ctx.fillStyle = '#333';
      ctx.beginPath();
      ctx.ellipse(0, 0, bf.size * 0.08, bf.size * 0.3, 0, 0, Math.PI * 2);
      ctx.fill();

      // Head
      ctx.beginPath();
      ctx.arc(bf.size * 0.1, 0, bf.size * 0.08, 0, Math.PI * 2);
      ctx.fill();

      // Antennae
      ctx.strokeStyle = '#333';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(bf.size * 0.1, -bf.size * 0.05);
      ctx.quadraticCurveTo(bf.size * 0.3, -bf.size * 0.2, bf.size * 0.25, -bf.size * 0.3);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(bf.size * 0.1, bf.size * 0.05);
      ctx.quadraticCurveTo(bf.size * 0.3, bf.size * 0.2, bf.size * 0.25, bf.size * 0.3);
      ctx.stroke();

      ctx.restore();
    };

    const updateDucks = (family: DuckFamily, width: number) => {
      const leader = family.leader;

      // Move leader
      leader.x += family.direction * 0.8;
      leader.waddle += 0.1;

      // Update blinking
      leader.blinkTimer--;
      if (leader.blinkTimer <= 0) {
        if (!leader.isBlinking) {
          leader.isBlinking = true;
          leader.blinkTimer = 10;
        } else {
          leader.isBlinking = false;
          leader.blinkTimer = 100 + Math.random() * 200;
        }
      }

      // Update quacking
      leader.quackTimer--;
      if (leader.quackTimer <= 0) {
        if (!leader.isQuacking) {
          leader.isQuacking = true;
          leader.quackTimer = 5 + Math.random() * 10;
        } else {
          leader.isQuacking = false;
          leader.quackTimer = 200 + Math.random() * 400;
        }
      }

      // Reset when off screen
      if ((family.direction === 1 && leader.x > width + 100) ||
          (family.direction === -1 && leader.x < -100)) {
        leader.x = family.direction === 1 ? -100 : width + 100;
        family.pathY = getRandomSideY(canvas.height);
        leader.y = family.pathY;
      }

      // Ducklings follow
      for (let i = 1; i < family.ducks.length; i++) {
        const duckling = family.ducks[i];
        const target = family.ducks[i - 1];
        const followDist = 25 + duckling.followIndex * 3;

        // Calculate where the duckling should be (behind the target in the parade direction)
        const targetPosX = target.x - family.direction * followDist;
        const dx = targetPosX - duckling.x;
        const dy = target.y + Math.sin(timeRef * 0.02 + i) * 3 - duckling.y;

        // Smooth following - clamp movement to prevent overshooting
        const moveSpeed = Math.min(Math.abs(dx) * 0.08, 2.0);
        duckling.x += Math.sign(dx) * moveSpeed;
        duckling.y += dy * 0.04;
        duckling.waddle += 0.1;

        // Update blinking
        duckling.blinkTimer--;
        if (duckling.blinkTimer <= 0) {
          duckling.isBlinking = !duckling.isBlinking;
          duckling.blinkTimer = duckling.isBlinking ? 8 : (80 + Math.random() * 150);
        }

        // Update quacking (less frequent for ducklings)
        duckling.quackTimer--;
        if (duckling.quackTimer <= 0) {
          duckling.isQuacking = !duckling.isQuacking;
          duckling.quackTimer = duckling.isQuacking ? 3 : (300 + Math.random() * 500);
        }

        // Occasional wing flap
        if (Math.random() < 0.002) {
          duckling.wingFlap = Math.PI;
        }
        if (duckling.wingFlap > 0) {
          duckling.wingFlap -= 0.2;
        }
      }

      // Add ripples occasionally
      if (Math.random() < 0.03) {
        const randomDuck = family.ducks[Math.floor(Math.random() * family.ducks.length)];
        ripples.push({
          x: randomDuck.x,
          y: randomDuck.y + randomDuck.size * 0.3,
          radius: 0,
          maxRadius: 15 + Math.random() * 10,
          opacity: 0.3,
        });
      }
    };

    const updateButterfly = (bf: Butterfly, width: number, height: number) => {
      // Update flutter phase for bobbing motion
      bf.flutterPhase += bf.flutterSpeed;
      bf.turnTimer++;

      // Handle hovering behavior (occasional pause)
      if (bf.isHovering) {
        bf.hoverTimer++;
        if (bf.hoverTimer > 40 + Math.random() * 30) {
          bf.isHovering = false;
          bf.hoverTimer = 0;
          // Pick new random direction after hover
          const angle = Math.random() * Math.PI * 2;
          bf.vx = Math.cos(angle) * bf.baseSpeed;
          bf.vy = Math.sin(angle) * bf.baseSpeed;
          bf.targetX = bf.x + Math.cos(angle) * 100;
          bf.targetY = bf.y + Math.sin(angle) * 100;
        }
        // Subtle drift while hovering
        bf.x += Math.sin(bf.flutterPhase * 2) * 0.3;
        bf.y += Math.cos(bf.flutterPhase * 1.5) * 0.2;
      } else {
        // Random chance to start hovering
        if (Math.random() < 0.003) {
          bf.isHovering = true;
          bf.hoverTimer = 0;
        }

        // Frequent erratic direction changes (like real butterflies)
        if (bf.turnTimer >= bf.turnInterval) {
          bf.turnTimer = 0;
          bf.turnInterval = 20 + Math.floor(Math.random() * 50);

          // Add random turn angle (butterflies don't fly straight)
          const turnAngle = (Math.random() - 0.5) * Math.PI * 0.8;
          const currentAngle = Math.atan2(bf.vy, bf.vx);
          const newAngle = currentAngle + turnAngle;
          const speed = bf.baseSpeed * (0.7 + Math.random() * 0.6);
          bf.vx = Math.cos(newAngle) * speed;
          bf.vy = Math.sin(newAngle) * speed;
          bf.targetX = bf.x + Math.cos(newAngle) * 100;
          bf.targetY = bf.y + Math.sin(newAngle) * 100;
        }

        // Add constant flutter/bobbing (vertical oscillation while flying)
        const flutter = Math.sin(bf.flutterPhase * 3) * 0.8;
        const sideFlutter = Math.cos(bf.flutterPhase * 2.3) * 0.4;

        bf.x += bf.vx + sideFlutter;
        bf.y += bf.vy + flutter;
      }

      // Screen boundary handling - gentle curves back into view
      const margin = 50;
      if (bf.x < margin) {
        bf.vx = Math.abs(bf.vx) * 0.8 + 0.3;
      } else if (bf.x > width - margin) {
        bf.vx = -Math.abs(bf.vx) * 0.8 - 0.3;
      }
      if (bf.y < margin) {
        bf.vy = Math.abs(bf.vy) * 0.8 + 0.2;
      } else if (bf.y > height * 0.6) {
        bf.vy = -Math.abs(bf.vy) * 0.8 - 0.2;
      }
    };

    const animate = () => {
      const { width, height } = canvas;
      timeRef++;

      // Clear canvas
      ctx.clearRect(0, 0, width, height);

      // Draw background
      drawBackground(ctx, width, height);

      // Draw grass
      grassPatches.forEach(grass => drawGrass(ctx, grass));

      // Update and draw ripples
      ripples = ripples.filter(r => {
        r.radius += 0.5;
        r.opacity = 0.3 * (1 - r.radius / r.maxRadius);
        drawRipple(ctx, r);
        return r.radius < r.maxRadius;
      });

      // Update and draw duck families
      families.forEach(family => {
        updateDucks(family, width);
        // Draw ducks from back to front
        for (let i = family.ducks.length - 1; i >= 0; i--) {
          drawDuck(ctx, family.ducks[i], family.direction);
        }
      });

      // Update and draw butterflies
      butterflies.forEach(bf => {
        updateButterfly(bf, width, height);
        drawButterfly(ctx, bf);
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
