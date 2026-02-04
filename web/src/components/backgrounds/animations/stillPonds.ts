/**
 * Still Ponds Animation
 *
 * Serene pond scene with lily pads, ripples, koi fish, and dragonflies.
 * Elements are positioned primarily on the sides, leaving center clear.
 * Optimized for smooth, graceful movement.
 */

import { useEffect, RefObject } from 'react';

interface LilyPad {
  x: number;
  y: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
  hasFlower: boolean;
  flowerColor: string;
  flowerPhase: number;
  bobPhase: number;
  bobSpeed: number;
}

interface Ripple {
  x: number;
  y: number;
  radius: number;
  maxRadius: number;
  opacity: number;
  speed: number;
}

interface Spot {
  x: number;
  y: number;
  size: number;
}

interface KoiFish {
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  size: number;
  speed: number;
  baseSpeed: number;
  angle: number;
  targetAngle: number;
  angularVelocity: number; // Current turning rate (momentum)
  tailPhase: number;
  tailAmplitude: number; // Dynamic amplitude based on swimming
  bodyPhase: number; // For body wave animation
  maxTurnRate: number; // Maximum turning speed (varies by fish)
  color: 'orange' | 'white' | 'gold' | 'red';
  pattern: 'solid' | 'spotted' | 'calico';
  spots: Spot[];
  depth: number;
}

interface Dragonfly {
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  wingPhase: number;
  size: number;
  color: string;
  hoverTime: number;
  state: 'flying' | 'hovering';
}


export function useStillPonds(
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
    let lilyPads: LilyPad[] = [];
    let ripples: Ripple[] = [];
    let koiFish: KoiFish[] = [];
    let dragonflies: Dragonfly[] = [];
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

    // Calculate the body half-width at a given x position along the fish
    // The body is tapered: widest at front, narrowing toward tail
    const getBodyHalfWidthAtX = (x: number, size: number): number => {
      // Body profile key points (x position -> half-width as fraction of size)
      // Head area: x = 0.4 -> width ~0.10
      // Widest: x = 0.25 -> width ~0.16
      // Mid: x = 0.0 -> width ~0.14
      // Back: x = -0.2 -> width ~0.11
      // Rear: x = -0.35 -> width ~0.06
      const xNorm = x / size;

      if (xNorm > 0.4) return size * 0.08; // Head tip
      if (xNorm > 0.25) {
        // Head to widest: interpolate 0.10 -> 0.16
        const t = (xNorm - 0.25) / 0.15;
        return size * (0.16 - t * 0.06);
      }
      if (xNorm > 0.0) {
        // Widest to mid: interpolate 0.16 -> 0.14
        const t = (xNorm - 0.0) / 0.25;
        return size * (0.14 + t * 0.02);
      }
      if (xNorm > -0.2) {
        // Mid to back: interpolate 0.14 -> 0.11
        const t = (xNorm - (-0.2)) / 0.2;
        return size * (0.11 + t * 0.03);
      }
      if (xNorm > -0.35) {
        // Back to rear: interpolate 0.11 -> 0.06
        const t = (xNorm - (-0.35)) / 0.15;
        return size * (0.06 + t * 0.05);
      }
      return size * 0.04; // Tail area
    };

    const generateSpots = (size: number, pattern: string): Spot[] => {
      if (pattern === 'solid') return [];
      const spots: Spot[] = [];
      const count = pattern === 'calico' ? 5 : 3;
      for (let i = 0; i < count; i++) {
        // Place spots within the main body area only
        // x: from head area to mid-body (avoiding tail)
        const spotX = size * 0.2 - Math.random() * size * 0.5; // From 0.2 to -0.3 of size
        const spotSize = size * 0.05 + Math.random() * size * 0.04; // Slightly smaller spots

        // Calculate maximum y-offset based on body width at this x position
        // Leave margin for the spot radius so spots don't exceed body edge
        const bodyHalfWidth = getBodyHalfWidthAtX(spotX, size);
        const maxYOffset = Math.max(0, bodyHalfWidth - spotSize - size * 0.01); // Small safety margin

        // Generate y within the constrained range
        const spotY = (Math.random() - 0.5) * 2 * maxYOffset;

        spots.push({
          x: spotX,
          y: spotY,
          size: spotSize,
        });
      }
      return spots;
    };

    const initializeScene = () => {
      const { width, height } = canvas;

      // Create lily pads
      lilyPads = [];
      const lilyCount = Math.floor(width / 150);
      for (let i = 0; i < lilyCount; i++) {
        lilyPads.push({
          x: getRandomSidePosition(width),
          y: Math.random() * height,
          size: 30 + Math.random() * 40,
          rotation: Math.random() * Math.PI * 2,
          rotationSpeed: (Math.random() - 0.5) * 0.0005,
          hasFlower: Math.random() > 0.6,
          flowerColor: ['#FFB6C1', '#FF69B4', '#FFF0F5', '#FFE4E1'][Math.floor(Math.random() * 4)],
          flowerPhase: Math.random() * Math.PI * 2,
          bobPhase: Math.random() * Math.PI * 2,
          bobSpeed: 0.3 + Math.random() * 0.3,
        });
      }

      // Create koi fish with pre-generated spots (larger to scale with lily pads)
      koiFish = [];
      const fishCount = Math.floor(width / 300);
      for (let i = 0; i < fishCount; i++) {
        const startX = getRandomSidePosition(width);
        const size = 80 + Math.random() * 60; // 2x bigger koi fish
        const pattern = ['solid', 'spotted', 'calico'][Math.floor(Math.random() * 3)] as KoiFish['pattern'];
        const angle = Math.random() * Math.PI * 2;
        const baseSpeed = 0.3 + Math.random() * 0.25;
        koiFish.push({
          x: startX,
          y: Math.random() * height,
          targetX: getRandomSidePosition(width),
          targetY: Math.random() * height,
          size,
          speed: baseSpeed,
          baseSpeed,
          angle,
          targetAngle: angle,
          angularVelocity: 0,
          tailPhase: Math.random() * Math.PI * 2,
          tailAmplitude: 1.0,
          bodyPhase: Math.random() * Math.PI * 2,
          maxTurnRate: 0.012 + Math.random() * 0.008, // Gentle max turn rate
          color: ['orange', 'white', 'gold', 'red'][Math.floor(Math.random() * 4)] as KoiFish['color'],
          pattern,
          spots: generateSpots(size, pattern),
          depth: 0.3 + Math.random() * 0.5,
        });
      }

      // Create dragonflies
      dragonflies = [];
      const dragonflyCount = Math.floor(width / 500);
      for (let i = 0; i < dragonflyCount; i++) {
        dragonflies.push({
          x: getRandomSidePosition(width),
          y: Math.random() * height * 0.5,
          targetX: getRandomSidePosition(width),
          targetY: Math.random() * height * 0.5,
          wingPhase: Math.random() * Math.PI * 2,
          size: 15 + Math.random() * 10,
          color: ['#DC143C', '#FF6347', '#FFD700', '#FF4500', '#FF1493'][Math.floor(Math.random() * 5)], // Bright contrasting colors
          hoverTime: 0,
          state: 'hovering',
        });
      }

      ripples = [];
    };

    const drawWaterBackground = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
      const waterGradient = ctx.createLinearGradient(0, 0, 0, height);
      if (darkMode) {
        waterGradient.addColorStop(0, '#1a3a4a');
        waterGradient.addColorStop(0.5, '#0d2a35');
        waterGradient.addColorStop(1, '#051520');
      } else {
        waterGradient.addColorStop(0, '#87CEEB');
        waterGradient.addColorStop(0.5, '#5BA3C0');
        waterGradient.addColorStop(1, '#3A7D9A');
      }
      ctx.fillStyle = waterGradient;
      ctx.fillRect(0, 0, width, height);

      // Subtle water caustics - slower movement
      ctx.globalAlpha = 0.03;
      for (let i = 0; i < 15; i++) {
        const x = (Math.sin(timeRef * 0.0001 + i * 0.7) * 0.5 + 0.5) * width;
        const y = (Math.cos(timeRef * 0.00008 + i * 0.5) * 0.5 + 0.5) * height;
        const gradient = ctx.createRadialGradient(x, y, 0, x, y, 120);
        gradient.addColorStop(0, '#ffffff');
        gradient.addColorStop(1, 'transparent');
        ctx.fillStyle = gradient;
        ctx.fillRect(x - 120, y - 120, 240, 240);
      }
      ctx.globalAlpha = 1;
    };

    const drawLilyPad = (ctx: CanvasRenderingContext2D, pad: LilyPad) => {
      const bobOffset = Math.sin(timeRef * 0.0005 * pad.bobSpeed + pad.bobPhase) * 1.5;

      ctx.save();
      ctx.translate(pad.x, pad.y + bobOffset);
      ctx.rotate(pad.rotation);

      // Shadow
      ctx.fillStyle = 'rgba(0, 0, 0, 0.08)';
      ctx.beginPath();
      ctx.ellipse(2, 2, pad.size, pad.size * 0.85, 0, 0, Math.PI * 2);
      ctx.fill();

      // Main lily pad
      const padGradient = ctx.createRadialGradient(
        -pad.size * 0.3, -pad.size * 0.3, 0,
        0, 0, pad.size
      );
      padGradient.addColorStop(0, darkMode ? '#2d5a3a' : '#4CAF50');
      padGradient.addColorStop(0.5, darkMode ? '#1e4a2a' : '#388E3C');
      padGradient.addColorStop(1, darkMode ? '#153a20' : '#2E7D32');

      ctx.fillStyle = padGradient;
      ctx.beginPath();
      ctx.ellipse(0, 0, pad.size, pad.size * 0.85, 0, 0, Math.PI * 2);
      ctx.fill();

      // Notch
      ctx.fillStyle = darkMode ? '#0d2a35' : '#5BA3C0';
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(pad.size * 0.3, -pad.size * 0.1);
      ctx.lineTo(pad.size, 0);
      ctx.lineTo(pad.size * 0.3, pad.size * 0.1);
      ctx.closePath();
      ctx.fill();

      // Veins
      ctx.strokeStyle = darkMode ? 'rgba(100, 180, 100, 0.2)' : 'rgba(200, 230, 200, 0.3)';
      ctx.lineWidth = 1;
      for (let i = 0; i < 8; i++) {
        const angle = (i / 8) * Math.PI * 2 + Math.PI / 8;
        if (Math.abs(angle) > 0.3) {
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(Math.cos(angle) * pad.size * 0.8, Math.sin(angle) * pad.size * 0.7);
          ctx.stroke();
        }
      }

      // Lotus flower
      if (pad.hasFlower) {
        const flowerBob = Math.sin(timeRef * 0.0008 + pad.flowerPhase) * 0.5;
        const petalCount = 8;
        const petalLength = pad.size * 0.4;
        const openAmount = 0.85 + Math.sin(timeRef * 0.0002 + pad.flowerPhase) * 0.05;

        for (let i = 0; i < petalCount; i++) {
          const angle = (i / petalCount) * Math.PI * 2;

          ctx.save();
          ctx.translate(0, -5 + flowerBob);
          ctx.rotate(angle);

          const petalGradient = ctx.createLinearGradient(0, 0, petalLength, 0);
          petalGradient.addColorStop(0, '#FFF8DC');
          petalGradient.addColorStop(0.5, pad.flowerColor);
          petalGradient.addColorStop(1, pad.flowerColor);

          ctx.fillStyle = petalGradient;
          ctx.beginPath();
          ctx.ellipse(petalLength * 0.5 * openAmount, 0, petalLength * 0.5, petalLength * 0.2, 0, 0, Math.PI * 2);
          ctx.fill();

          ctx.restore();
        }

        ctx.fillStyle = '#FFD700';
        ctx.beginPath();
        ctx.arc(0, -5 + flowerBob, pad.size * 0.1, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.restore();
    };

    const drawKoiFish = (ctx: CanvasRenderingContext2D, fish: KoiFish) => {
      ctx.save();
      ctx.translate(fish.x, fish.y);
      ctx.rotate(fish.angle);

      ctx.globalAlpha = 0.5 + fish.depth * 0.5;

      const size = fish.size;

      let bodyColor: string, spotColor: string;
      switch (fish.color) {
        case 'orange':
          bodyColor = '#FF6B35';
          spotColor = '#FFFFFF';
          break;
        case 'white':
          bodyColor = '#FFFAF0';
          spotColor = '#FF6B35';
          break;
        case 'gold':
          bodyColor = '#FFD700';
          spotColor = '#FF8C00';
          break;
        case 'red':
          bodyColor = '#DC143C';
          spotColor = '#FFFFFF';
          break;
      }

      // Shadow (tapered teardrop shape for overhead view)
      ctx.fillStyle = `rgba(0, 0, 0, ${0.08 * fish.depth})`;
      ctx.beginPath();
      ctx.moveTo(size * 0.55 + 3, 3);
      ctx.bezierCurveTo(size * 0.25 + 3, -size * 0.14 + 3, -size * 0.2 + 3, -size * 0.08 + 3, -size * 0.45 + 3, 3);
      ctx.bezierCurveTo(-size * 0.2 + 3, size * 0.08 + 3, size * 0.25 + 3, size * 0.14 + 3, size * 0.55 + 3, 3);
      ctx.fill();

      // Propagating wave - phase increases toward tail (wave travels head to tail)
      // Each segment has progressively delayed phase, amplitude increases toward tail
      const wavePhase = fish.bodyPhase;
      const amp = fish.tailAmplitude;

      // Calculate wave at different body positions (phase delay + amplitude increase)
      // Positions: 0 = head, 1 = tail base
      const waveAt = (pos: number) => {
        const phaseDelay = pos * 1.2; // Wave propagates with delay
        const ampScale = pos * pos; // Amplitude increases quadratically toward tail
        return Math.sin(wavePhase - phaseDelay) * 4 * amp * ampScale;
      };

      // Body wave at key points along the fish
      const wave20 = waveAt(0.2);  // Front body
      const wave40 = waveAt(0.4);  // Mid-front
      const wave60 = waveAt(0.6);  // Mid-back
      const wave80 = waveAt(0.8);  // Rear body
      const wave100 = waveAt(1.0); // Tail base

      // Main body - wave applied with propagating phase
      const bodyGradient = ctx.createRadialGradient(
        size * 0.1, 0, 0,
        0, 0, size * 0.65
      );
      bodyGradient.addColorStop(0, bodyColor);
      bodyGradient.addColorStop(0.6, bodyColor);
      bodyGradient.addColorStop(1, darkMode ? '#333' : '#666');

      ctx.fillStyle = bodyGradient;
      ctx.beginPath();
      // Head (pointed snout)
      ctx.moveTo(size * 0.55, 0);
      // Upper body contour - widest at front, tapering toward tail
      ctx.bezierCurveTo(
        size * 0.45, -size * 0.10 + wave20 * 0.3,   // Head curves out
        size * 0.25, -size * 0.16 + wave20,         // Widest point (front body)
        size * 0.0, -size * 0.14 + wave40           // Still wide at mid-front
      );
      ctx.bezierCurveTo(
        -size * 0.2, -size * 0.11 + wave60,         // Starting to taper
        -size * 0.35, -size * 0.06 + wave80,        // Noticeably narrower
        -size * 0.48, -size * 0.025 + wave100       // Narrow caudal peduncle
      );
      // Caudal peduncle (narrow tail stalk)
      ctx.lineTo(-size * 0.48, size * 0.025 + wave100);
      // Lower body contour (mirror) - tapering from tail to widest point
      ctx.bezierCurveTo(
        -size * 0.35, size * 0.06 + wave80,         // Narrower rear
        -size * 0.2, size * 0.11 + wave60,          // Widening
        size * 0.0, size * 0.14 + wave40            // Wide mid-front
      );
      ctx.bezierCurveTo(
        size * 0.25, size * 0.16 + wave20,          // Widest point
        size * 0.45, size * 0.10 + wave20 * 0.3,    // Head curves in
        size * 0.55, 0                               // Back to snout
      );
      ctx.closePath();
      ctx.fill();

      // Tail fin - overhead view: thin forked shape, not fanned out
      // The tail is like two thin lobes that angle slightly outward
      const tailWave = waveAt(1.15); // Continue the wave into the tail
      const tailTip = waveAt(1.4);   // Tail tip has most displacement
      const tailSpread = size * 0.04; // Very slight spread from overhead

      ctx.beginPath();
      // Start from tail base (where body ends)
      ctx.moveTo(-size * 0.48, wave100);

      // Upper lobe of tail - thin line with slight curve
      ctx.quadraticCurveTo(
        -size * 0.7, tailWave - tailSpread * 0.5,
        -size * 0.9, tailTip - tailSpread
      );
      // Tail tip curves slightly
      ctx.quadraticCurveTo(
        -size * 0.95, tailTip,
        -size * 0.9, tailTip + tailSpread
      );
      // Lower lobe back to base
      ctx.quadraticCurveTo(
        -size * 0.7, tailWave + tailSpread * 0.5,
        -size * 0.48, wave100
      );
      ctx.closePath();

      const tailGradient = ctx.createLinearGradient(-size * 0.48, 0, -size * 0.9, 0);
      tailGradient.addColorStop(0, bodyColor);
      tailGradient.addColorStop(0.6, bodyColor);
      tailGradient.addColorStop(1, 'rgba(0, 0, 0, 0.15)');
      ctx.fillStyle = tailGradient;
      ctx.fill();

      // Dorsal stripe (darker line down center, follows propagating wave)
      ctx.strokeStyle = `rgba(0, 0, 0, 0.12)`;
      ctx.lineWidth = size * 0.05;
      ctx.beginPath();
      ctx.moveTo(size * 0.35, wave20 * 0.5);
      ctx.bezierCurveTo(
        size * 0.1, wave40,
        -size * 0.15, wave60,
        -size * 0.35, wave80
      );
      ctx.stroke();

      // Pre-generated spots - follow propagating wave based on position
      if (fish.spots.length > 0) {
        ctx.fillStyle = spotColor;
        fish.spots.forEach(spot => {
          // Calculate position ratio (0 at head, 1 at tail base)
          const positionRatio = Math.max(0, Math.min(1, (size * 0.3 - spot.x) / (size * 0.8)));
          const spotWaveOffset = waveAt(positionRatio);
          ctx.beginPath();
          ctx.arc(spot.x, spot.y + spotWaveOffset, spot.size, 0, Math.PI * 2);
          ctx.fill();
        });
      }

      // Pectoral fins (overhead - extend outward from sides of sleeker body)
      const finWave = Math.sin(fish.tailPhase * 0.7) * 2.5;
      ctx.fillStyle = bodyColor;
      ctx.globalAlpha *= 0.7;

      // Left pectoral fin - drawn as curved shape extending outward (sleeker)
      ctx.beginPath();
      ctx.moveTo(size * 0.2, -size * 0.12);
      ctx.bezierCurveTo(size * 0.3, -size * 0.25 - finWave * 0.4, size * 0.12, -size * 0.35 - finWave * 0.8, -size * 0.02, -size * 0.26 - finWave * 0.25);
      ctx.bezierCurveTo(size * 0.06, -size * 0.2, size * 0.14, -size * 0.14, size * 0.2, -size * 0.12);
      ctx.closePath();
      ctx.fill();

      // Right pectoral fin - drawn as curved shape extending outward (sleeker)
      ctx.beginPath();
      ctx.moveTo(size * 0.2, size * 0.12);
      ctx.bezierCurveTo(size * 0.3, size * 0.25 + finWave * 0.4, size * 0.12, size * 0.35 + finWave * 0.8, -size * 0.02, size * 0.26 + finWave * 0.25);
      ctx.bezierCurveTo(size * 0.06, size * 0.2, size * 0.14, size * 0.14, size * 0.2, size * 0.12);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 0.5 + fish.depth * 0.5;

      // Head (sleeker, more pointed)
      ctx.fillStyle = bodyColor;
      ctx.beginPath();
      ctx.ellipse(size * 0.4, 0, size * 0.2, size * 0.12, 0, 0, Math.PI * 2);
      ctx.fill();

      // Eyes (both visible from overhead - adjusted for sleeker head)
      ctx.fillStyle = '#000000';
      ctx.beginPath();
      ctx.arc(size * 0.42, -size * 0.07, size * 0.035, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(size * 0.42, size * 0.07, size * 0.035, 0, Math.PI * 2);
      ctx.fill();

      // Eye highlights
      ctx.fillStyle = '#FFFFFF';
      ctx.beginPath();
      ctx.arc(size * 0.43, -size * 0.075, size * 0.012, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(size * 0.43, size * 0.065, size * 0.012, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
    };

    const drawDragonfly = (ctx: CanvasRenderingContext2D, dragonfly: Dragonfly) => {
      ctx.save();
      ctx.translate(dragonfly.x, dragonfly.y);

      const size = dragonfly.size;
      // Wing shimmer effect from flapping
      const wingFlap = Math.sin(timeRef * 0.15 + dragonfly.wingPhase);
      const wingOpacity = 0.3 + Math.abs(wingFlap) * 0.15;

      const dx = dragonfly.targetX - dragonfly.x;
      const dy = dragonfly.targetY - dragonfly.y;
      const angle = Math.atan2(dy, dx);
      ctx.rotate(angle);

      // Simple symmetrical dragonfly - top view
      // Wings extend perpendicular to body (sideways) - iridescent shimmer
      const iridescence = Math.sin(timeRef * 0.1 + dragonfly.wingPhase) * 20;
      ctx.fillStyle = `rgba(${220 + iridescence}, ${235 + iridescence}, 255, ${wingOpacity + 0.15})`;
      ctx.strokeStyle = `rgba(180, 200, 230, ${wingOpacity + 0.25})`;
      ctx.lineWidth = 0.6;

      // Wing dimensions - elongated ovals extending sideways
      const wingLength = size * 0.8;
      const wingWidth = size * 0.15;

      // Forewings (front pair) - extend perpendicular, slightly forward
      // Upper left forewing
      ctx.beginPath();
      ctx.ellipse(size * 0.08, -wingLength * 0.5, wingWidth, wingLength * 0.5, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // Upper right forewing
      ctx.beginPath();
      ctx.ellipse(size * 0.08, wingLength * 0.5, wingWidth, wingLength * 0.5, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // Hindwings (back pair) - extend perpendicular, slightly behind, slightly wider
      const hindWingLength = size * 0.75;
      const hindWingWidth = size * 0.18;

      // Upper left hindwing
      ctx.beginPath();
      ctx.ellipse(-size * 0.08, -hindWingLength * 0.5, hindWingWidth, hindWingLength * 0.5, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // Upper right hindwing
      ctx.beginPath();
      ctx.ellipse(-size * 0.08, hindWingLength * 0.5, hindWingWidth, hindWingLength * 0.5, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // Wing veins - simple center line through each wing
      ctx.strokeStyle = `rgba(120, 160, 210, ${wingOpacity * 0.7})`;
      ctx.lineWidth = 0.4;
      // Forewing veins
      ctx.beginPath();
      ctx.moveTo(size * 0.08, -size * 0.1);
      ctx.lineTo(size * 0.08, -wingLength);
      ctx.moveTo(size * 0.08, size * 0.1);
      ctx.lineTo(size * 0.08, wingLength);
      ctx.stroke();
      // Hindwing veins
      ctx.beginPath();
      ctx.moveTo(-size * 0.08, -size * 0.1);
      ctx.lineTo(-size * 0.08, -hindWingLength);
      ctx.moveTo(-size * 0.08, size * 0.1);
      ctx.lineTo(-size * 0.08, hindWingLength);
      ctx.stroke();

      // Body - long abdomen extending backward
      const bodyGradient = ctx.createLinearGradient(-size * 1.4, 0, size * 0.3, 0);
      bodyGradient.addColorStop(0, dragonfly.color);
      bodyGradient.addColorStop(0.85, dragonfly.color);
      bodyGradient.addColorStop(1, '#000000');
      ctx.fillStyle = bodyGradient;

      // Abdomen - longer, thicker tail (10 segments, more gradual taper)
      for (let i = 0; i < 10; i++) {
        const segX = -size * 0.15 - i * size * 0.13;
        const segRadius = size * 0.055 * (1 - i * 0.06);
        ctx.beginPath();
        ctx.arc(segX, 0, Math.max(segRadius, size * 0.022), 0, Math.PI * 2);
        ctx.fill();
      }

      // Thorax (larger middle section where wings attach)
      ctx.fillStyle = dragonfly.color;
      ctx.beginPath();
      ctx.ellipse(0, 0, size * 0.14, size * 0.11, 0, 0, Math.PI * 2);
      ctx.fill();

      // Head - large with prominent compound eyes
      ctx.beginPath();
      ctx.arc(size * 0.2, 0, size * 0.09, 0, Math.PI * 2);
      ctx.fill();

      // Compound eyes (large, prominent from overhead - adjusted for larger head)
      ctx.fillStyle = darkMode ? 'rgba(40, 60, 70, 0.9)' : 'rgba(30, 50, 60, 0.9)';
      ctx.beginPath();
      ctx.ellipse(size * 0.24, -size * 0.06, size * 0.06, size * 0.05, -0.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.ellipse(size * 0.24, size * 0.06, size * 0.06, size * 0.05, 0.2, 0, Math.PI * 2);
      ctx.fill();

      // Eye highlights
      ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
      ctx.beginPath();
      ctx.arc(size * 0.27, -size * 0.07, size * 0.018, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(size * 0.27, size * 0.05, size * 0.018, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
    };


    const drawRipple = (ctx: CanvasRenderingContext2D, ripple: Ripple) => {
      ctx.strokeStyle = darkMode
        ? `rgba(150, 200, 220, ${ripple.opacity})`
        : `rgba(255, 255, 255, ${ripple.opacity})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(ripple.x, ripple.y, ripple.radius, 0, Math.PI * 2);
      ctx.stroke();
    };

    const updateFish = (fish: KoiFish) => {
      const dx = fish.targetX - fish.x;
      const dy = fish.targetY - fish.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      // Pick new target when close enough - but bias toward current heading direction
      if (dist < 50) {
        // Generate a target that's roughly in front of the fish (within ~90 degrees)
        // This prevents fish from making sharp U-turns
        const forwardBias = 0.7; // How much to bias toward current direction
        const randomAngle = fish.angle + (Math.random() - 0.5) * Math.PI * 1.2; // +/- 108 degrees from current heading
        const targetDist = 150 + Math.random() * 200;

        // Calculate biased target position
        const biasedX = fish.x + Math.cos(randomAngle) * targetDist;
        const biasedY = fish.y + Math.sin(randomAngle) * targetDist;

        // Mix with random position for occasional exploration
        const randomX = getRandomSidePosition(canvas.width);
        const randomY = Math.random() * canvas.height;

        fish.targetX = biasedX * forwardBias + randomX * (1 - forwardBias);
        fish.targetY = biasedY * forwardBias + randomY * (1 - forwardBias);

        // Keep target in bounds
        fish.targetX = Math.max(50, Math.min(canvas.width - 50, fish.targetX));
        fish.targetY = Math.max(50, Math.min(canvas.height - 50, fish.targetY));
      }

      // Also occasionally nudge target slightly for natural wandering
      if (Math.random() < 0.005) {
        fish.targetX += (Math.random() - 0.5) * 100;
        fish.targetY += (Math.random() - 0.5) * 80;
        fish.targetX = Math.max(50, Math.min(canvas.width - 50, fish.targetX));
        fish.targetY = Math.max(50, Math.min(canvas.height - 50, fish.targetY));
      }

      // Calculate desired angle toward target
      fish.targetAngle = Math.atan2(dy, dx);

      // Calculate angle difference (normalized to -PI to PI)
      const angleDiff = Math.atan2(
        Math.sin(fish.targetAngle - fish.angle),
        Math.cos(fish.targetAngle - fish.angle)
      );

      // Apply very gradual angular acceleration (momentum-based turning)
      // Reduced acceleration for smoother, more natural turns
      const angularAcceleration = 0.0004; // Reduced from 0.0008
      // Target angular velocity is gentler - fish don't try to correct aggressively
      const targetAngularVelocity = Math.sign(angleDiff) * Math.min(Math.abs(angleDiff) * 0.01, fish.maxTurnRate);

      // Smoothly interpolate angular velocity with more inertia
      fish.angularVelocity += (targetAngularVelocity - fish.angularVelocity) * angularAcceleration * 60;

      // Clamp angular velocity to max turn rate
      fish.angularVelocity = Math.max(-fish.maxTurnRate, Math.min(fish.maxTurnRate, fish.angularVelocity));

      // Apply angular velocity to angle
      fish.angle += fish.angularVelocity;

      // Calculate how much the fish is currently turning (for tail animation)
      const turnIntensity = Math.abs(fish.angularVelocity) / fish.maxTurnRate;

      // Tail beat speed increases with swimming speed, amplitude increases when turning
      const tailSpeed = 0.08 + fish.speed * 0.15;
      fish.tailPhase += tailSpeed;

      // Tail amplitude: base + extra when turning (fish sweep tail harder to turn)
      fish.tailAmplitude = 0.8 + turnIntensity * 0.6;

      // Body wave follows tail but slightly slower
      fish.bodyPhase += tailSpeed * 0.7;

      // Speed varies slightly with tail beat (fish pulse forward with each stroke)
      const speedPulse = 1 + Math.sin(fish.tailPhase * 2) * 0.08;
      // Slow down slightly when turning sharply (more realistic)
      const turnSlowdown = 1 - turnIntensity * 0.3;
      fish.speed = fish.baseSpeed * speedPulse * turnSlowdown;

      // Move in direction of current angle
      fish.x += Math.cos(fish.angle) * fish.speed;
      fish.y += Math.sin(fish.angle) * fish.speed;
    };

    const updateDragonfly = (df: Dragonfly) => {
      const dx = df.targetX - df.x;
      const dy = df.targetY - df.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (df.state === 'flying') {
        if (dist < 15) {
          df.state = 'hovering';
          df.hoverTime = 120 + Math.random() * 180;
        } else {
          df.x += (dx / dist) * 1.2;
          df.y += (dy / dist) * 1.2;
        }
      } else {
        df.hoverTime--;
        df.x += Math.sin(timeRef * 0.005) * 0.15;
        df.y += Math.cos(timeRef * 0.007) * 0.1;

        if (df.hoverTime <= 0) {
          df.state = 'flying';
          df.targetX = getRandomSidePosition(canvas.width);
          df.targetY = Math.random() * canvas.height * 0.5;
        }
      }

      df.wingPhase += 0.1;
    };

    const animate = () => {
      const { width, height } = canvas;
      timeRef++;

      ctx.clearRect(0, 0, width, height);

      drawWaterBackground(ctx, width, height);

      // Ripples - less frequent
      if (Math.random() < 0.005) {
        ripples.push({
          x: getRandomSidePosition(width),
          y: Math.random() * height,
          radius: 0,
          maxRadius: 25 + Math.random() * 30,
          opacity: 0.4,
          speed: 0.3 + Math.random() * 0.3,
        });
      }

      ripples = ripples.filter(r => {
        r.radius += r.speed;
        r.opacity = 0.4 * (1 - r.radius / r.maxRadius);
        drawRipple(ctx, r);
        return r.radius < r.maxRadius;
      });

      // Fish
      koiFish.sort((a, b) => a.depth - b.depth);
      koiFish.forEach(fish => {
        updateFish(fish);
        drawKoiFish(ctx, fish);
      });

      // Lily pads
      lilyPads.forEach(pad => {
        pad.rotation += pad.rotationSpeed;
        drawLilyPad(ctx, pad);
      });

      // Dragonflies
      dragonflies.forEach(df => {
        updateDragonfly(df);
        drawDragonfly(ctx, df);
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
