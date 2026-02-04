/**
 * Sakura Redux Animation
 * Cherry blossom petals that fall and collect at the bottom of the screen.
 * Petals accumulate for 10 minutes before fading away.
 * Features a decorative cherry blossom branch from upper right corner.
 */

import { useRef, useEffect, useCallback } from 'react';

interface FallingPetal {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
  targetRotationSpeed: number;
  tilt: number;
  tiltSpeed: number;
  tiltPhase: number;
  swayPhase: number;
  opacity: number;
  flutterTimer: number;
  isDrifting: boolean;
  notchDepth: number;
}

interface GroundPetal {
  x: number;
  y: number;
  size: number;
  rotation: number;
  opacity: number;
  notchDepth: number;
  createdAt: number; // timestamp for fade-out timing
  layer: number; // for depth sorting
}

const COLLECTION_DURATION = 10 * 60 * 1000; // 10 minutes in milliseconds
const FADE_DURATION = 30 * 1000; // 30 seconds to fade out

export function useSakuraRedux(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const fallingPetalsRef = useRef<FallingPetal[]>([]);
  const groundPetalsRef = useRef<GroundPetal[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);
  const startTimeRef = useRef<number>(0);

  const createFallingPetal = useCallback((canvas: HTMLCanvasElement, startFromTop = true): FallingPetal => {
    return {
      x: Math.random() * canvas.width,
      y: startFromTop ? -20 - Math.random() * 40 : Math.random() * canvas.height * 0.5,
      vx: (Math.random() - 0.5) * 0.3,
      vy: 0.25 + Math.random() * 0.35,
      size: 10 + Math.random() * 10,
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.015,
      targetRotationSpeed: (Math.random() - 0.5) * 0.03,
      tilt: Math.random() * Math.PI,
      tiltSpeed: 0.015 + Math.random() * 0.025,
      tiltPhase: Math.random() * Math.PI * 2,
      swayPhase: Math.random() * Math.PI * 2,
      opacity: 0.6 + Math.random() * 0.3,
      flutterTimer: Math.random() * 80,
      isDrifting: Math.random() > 0.6,
      notchDepth: 0.15 + Math.random() * 0.15,
    };
  }, []);

  const createGroundPetal = useCallback((fallingPetal: FallingPetal, groundY: number): GroundPetal => {
    return {
      x: fallingPetal.x,
      y: groundY - Math.random() * 5, // Slight variation in landing position
      size: fallingPetal.size * (0.85 + Math.random() * 0.15), // Slightly varied size when landed
      rotation: fallingPetal.rotation + (Math.random() - 0.5) * 0.3, // Final rotation
      opacity: fallingPetal.opacity * 0.9,
      notchDepth: fallingPetal.notchDepth,
      createdAt: Date.now(),
      layer: Math.random(), // Random layer for depth
    };
  }, []);

  const drawPetal = useCallback((
    ctx: CanvasRenderingContext2D,
    x: number,
    y: number,
    size: number,
    rotation: number,
    notchDepth: number,
    alpha: number,
    isDark: boolean,
    tiltFactor: number = 1
  ) => {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rotation);
    ctx.scale(0.3 + tiltFactor * 0.7, 1);

    const s = size;
    const notch = notchDepth;

    // Sakura petal colors - soft pink like real cherry blossoms
    const baseColor = isDark
      ? { r: 255, g: 192, b: 210 }
      : { r: 255, g: 210, b: 225 };

    // Draw realistic sakura petal - soft oval with subtle notch at tip
    ctx.beginPath();

    // Start at the base (stem attachment point) - pointed tip
    ctx.moveTo(0, s * 0.55);

    // Right side - smooth outward curve forming rounded petal body
    ctx.bezierCurveTo(
      s * 0.25, s * 0.45,   // Control near base
      s * 0.48, s * 0.15,   // Control at widest point
      s * 0.45, -s * 0.15   // End approaching tip
    );

    // Right side approaching notch - curves inward smoothly
    ctx.bezierCurveTo(
      s * 0.42, -s * 0.32,  // Control curving toward tip
      s * 0.22, -s * 0.42,  // Control curving into notch
      s * 0.08, -s * (0.38 + notch * 0.4)  // Right edge of notch
    );

    // The notch itself - a gentle V or U shape characteristic of sakura
    ctx.quadraticCurveTo(
      0, -s * (0.32 + notch * 0.2),  // Bottom of notch (control point)
      -s * 0.08, -s * (0.38 + notch * 0.4)  // Left edge of notch
    );

    // Left side from notch - mirror of right side
    ctx.bezierCurveTo(
      -s * 0.22, -s * 0.42, // Control curving out of notch
      -s * 0.42, -s * 0.32, // Control approaching widest
      -s * 0.45, -s * 0.15  // Left side near tip
    );

    // Left side - smooth curve back to base
    ctx.bezierCurveTo(
      -s * 0.48, s * 0.15,  // Control at widest point
      -s * 0.25, s * 0.45,  // Control near base
      0, s * 0.55           // Back to base point
    );

    ctx.closePath();

    // Base fill - soft pink
    ctx.fillStyle = `rgba(${baseColor.r}, ${baseColor.g}, ${baseColor.b}, ${alpha})`;
    ctx.fill();

    // Add subtle radial gradient for natural depth
    const gradient = ctx.createRadialGradient(
      0, s * 0.25, 0,       // Center near base
      0, s * 0.05, s * 0.55 // Expand toward edges
    );
    gradient.addColorStop(0, `rgba(${baseColor.r - 25}, ${baseColor.g - 35}, ${baseColor.b - 25}, ${alpha * 0.35})`);
    gradient.addColorStop(0.4, `rgba(255, 255, 255, ${alpha * 0.08})`);
    gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');
    ctx.fillStyle = gradient;
    ctx.fill();

    // Add soft edge highlight for translucency effect
    const edgeGradient = ctx.createLinearGradient(-s * 0.4, 0, s * 0.4, 0);
    edgeGradient.addColorStop(0, `rgba(255, 255, 255, ${alpha * 0.12})`);
    edgeGradient.addColorStop(0.5, 'rgba(255, 255, 255, 0)');
    edgeGradient.addColorStop(1, `rgba(255, 255, 255, ${alpha * 0.12})`);
    ctx.fillStyle = edgeGradient;
    ctx.fill();

    // Draw delicate central vein - subtle and natural
    ctx.strokeStyle = `rgba(${baseColor.r - 45}, ${baseColor.g - 40}, ${baseColor.b - 30}, ${alpha * 0.18})`;
    ctx.lineWidth = 0.4;
    ctx.beginPath();
    ctx.moveTo(0, s * 0.45);
    ctx.quadraticCurveTo(s * 0.02, s * 0.1, 0, -s * 0.28);
    ctx.stroke();

    // Very subtle secondary veins
    ctx.strokeStyle = `rgba(${baseColor.r - 35}, ${baseColor.g - 30}, ${baseColor.b - 20}, ${alpha * 0.08})`;
    ctx.lineWidth = 0.3;
    ctx.beginPath();
    ctx.moveTo(0, s * 0.2);
    ctx.quadraticCurveTo(s * 0.12, s * 0.05, s * 0.2, -s * 0.12);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, s * 0.2);
    ctx.quadraticCurveTo(-s * 0.12, s * 0.05, -s * 0.2, -s * 0.12);
    ctx.stroke();

    ctx.restore();
  }, []);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    startTimeRef.current = Date.now();

    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    // Initialize falling petals
    const petalCount = Math.floor((canvas.width * canvas.height) / 30000);
    fallingPetalsRef.current = Array.from({ length: Math.max(10, petalCount) }, () =>
      createFallingPetal(canvas, false)
    );
    groundPetalsRef.current = [];

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;
      const currentTime = Date.now();

      const opacityMultiplier = opacity / 50;

      // Calculate ground level - starts at bottom, rises as petals accumulate
      const groundPetalCount = groundPetalsRef.current.length;
      const maxPileHeight = canvas.height * 0.25; // Max 25% of screen height
      const pileHeight = Math.min(groundPetalCount * 0.3, maxPileHeight);
      const groundLevel = canvas.height - 10 - pileHeight;

      // Draw ground petals first (behind falling ones)
      // Sort by layer for depth effect
      groundPetalsRef.current.sort((a, b) => a.layer - b.layer);

      groundPetalsRef.current = groundPetalsRef.current.filter((petal) => {
        const age = currentTime - petal.createdAt;

        // Calculate opacity based on age
        let petalOpacity = petal.opacity;
        if (age > COLLECTION_DURATION) {
          // Start fading after 10 minutes
          const fadeProgress = Math.min(1, (age - COLLECTION_DURATION) / FADE_DURATION);
          petalOpacity *= (1 - fadeProgress);

          if (fadeProgress >= 1) {
            return false; // Remove completely faded petals
          }
        }

        drawPetal(
          ctx,
          petal.x,
          petal.y,
          petal.size,
          petal.rotation,
          petal.notchDepth,
          petalOpacity * opacityMultiplier,
          darkMode,
          0.7 + Math.random() * 0.3 // Slight tilt variation for grounded petals
        );

        return true;
      });

      // Update and draw falling petals
      fallingPetalsRef.current.forEach((petal, index) => {
        // Update flutter timer
        petal.flutterTimer += 1;

        if (petal.flutterTimer > 70 + Math.random() * 50) {
          petal.flutterTimer = 0;
          petal.isDrifting = !petal.isDrifting;
          petal.targetRotationSpeed = (Math.random() - 0.5) * 0.04;
          petal.vx += (Math.random() - 0.5) * 0.5;
        }

        petal.rotationSpeed += (petal.targetRotationSpeed - petal.rotationSpeed) * 0.02;
        petal.tilt += petal.tiltSpeed;
        petal.tiltSpeed += Math.sin(timeRef.current * 1.5 + petal.tiltPhase) * 0.0008;
        petal.tiltSpeed = Math.max(0.01, Math.min(0.04, petal.tiltSpeed));

        const sway = Math.sin(timeRef.current * 0.6 + petal.swayPhase) * 0.35;
        const tiltInfluence = Math.sin(petal.tilt) * 0.25;

        if (petal.isDrifting) {
          petal.vx += sway * 0.06 + tiltInfluence * 0.08;
          petal.vy *= 0.985;
          petal.vy = Math.max(0.1, petal.vy);
        } else {
          petal.vx += sway * 0.025;
          petal.vy += 0.002;
          petal.vy = Math.min(0.6, petal.vy);
        }

        petal.vx *= 0.988;
        petal.x += petal.vx;
        petal.y += petal.vy;
        petal.rotation += petal.rotationSpeed + petal.vx * 0.015;

        // Check if petal has landed
        if (petal.y >= groundLevel) {
          // Add to ground pile
          groundPetalsRef.current.push(createGroundPetal(petal, groundLevel));
          // Reset falling petal
          fallingPetalsRef.current[index] = createFallingPetal(canvas, true);
          return;
        }

        // Reset if off screen horizontally
        if (petal.x < -40 || petal.x > canvas.width + 40) {
          fallingPetalsRef.current[index] = createFallingPetal(canvas, true);
          return;
        }

        const tiltFactor = Math.abs(Math.cos(petal.tilt));
        drawPetal(
          ctx,
          petal.x,
          petal.y,
          petal.size,
          petal.rotation,
          petal.notchDepth,
          petal.opacity * opacityMultiplier,
          darkMode,
          tiltFactor
        );
      });

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [canvasRef, darkMode, opacity, createFallingPetal, createGroundPetal, drawPetal, active]);
}
