/**
 * Sakura Petals Animation
 * Gentle falling cherry blossom petals
 */

import { useRef, useEffect, useCallback } from 'react';

interface Petal {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
  targetRotationSpeed: number;
  tilt: number; // 3D tilt angle
  tiltSpeed: number;
  tiltPhase: number;
  swayPhase: number;
  opacity: number;
  flutterTimer: number;
  isDrifting: boolean;
  notchDepth: number; // Depth of the heart-shaped notch (varies per petal)
}

export function useSakuraPetals(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const petalsRef = useRef<Petal[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  const createPetal = useCallback((canvas: HTMLCanvasElement, startFromTop = true): Petal => {
    return {
      x: Math.random() * canvas.width,
      y: startFromTop ? -20 - Math.random() * 40 : Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: 0.3 + Math.random() * 0.4,
      size: 10 + Math.random() * 10,
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.015,
      targetRotationSpeed: (Math.random() - 0.5) * 0.03,
      tilt: Math.random() * Math.PI,
      tiltSpeed: 0.015 + Math.random() * 0.025,
      tiltPhase: Math.random() * Math.PI * 2,
      swayPhase: Math.random() * Math.PI * 2,
      opacity: 0.5 + Math.random() * 0.35,
      flutterTimer: Math.random() * 80,
      isDrifting: Math.random() > 0.6,
      notchDepth: 0.15 + Math.random() * 0.15, // Heart notch varies 15-30% of petal height
    };
  }, []);

  const drawPetal = useCallback((
    ctx: CanvasRenderingContext2D,
    petal: Petal,
    isDark: boolean,
    opacityMultiplier: number
  ) => {
    ctx.save();
    ctx.translate(petal.x, petal.y);
    ctx.rotate(petal.rotation);

    // Apply 3D tilt effect - makes petal appear to tumble
    const tiltFactor = Math.abs(Math.cos(petal.tilt));
    ctx.scale(0.3 + tiltFactor * 0.7, 1);

    const s = petal.size;
    const alpha = petal.opacity * opacityMultiplier;
    const notch = petal.notchDepth;

    // Sakura petal colors - soft pink like real cherry blossoms
    const baseColor = isDark
      ? { r: 255, g: 192, b: 210 }
      : { r: 255, g: 210, b: 225 };

    // Draw realistic sakura petal - soft oval with subtle notch at tip
    // Real sakura petals are wider than tall, with a gentle cleft
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
    // Slightly darker/pinker toward the base, lighter toward edges
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
    // Slight curve in the vein for natural look
    ctx.quadraticCurveTo(s * 0.02, s * 0.1, 0, -s * 0.28);
    ctx.stroke();

    // Optional: Very subtle secondary veins
    ctx.strokeStyle = `rgba(${baseColor.r - 35}, ${baseColor.g - 30}, ${baseColor.b - 20}, ${alpha * 0.08})`;
    ctx.lineWidth = 0.3;
    // Right secondary vein
    ctx.beginPath();
    ctx.moveTo(0, s * 0.2);
    ctx.quadraticCurveTo(s * 0.12, s * 0.05, s * 0.2, -s * 0.12);
    ctx.stroke();
    // Left secondary vein
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

    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const petalCount = Math.floor((canvas.width * canvas.height) / 25000);
    petalsRef.current = Array.from({ length: petalCount }, () =>
      createPetal(canvas, false)
    );

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      petalsRef.current.forEach((petal, index) => {
        // Update flutter timer
        petal.flutterTimer += 1;

        // Occasionally change drift direction (simulates catching air currents)
        if (petal.flutterTimer > 70 + Math.random() * 50) {
          petal.flutterTimer = 0;
          petal.isDrifting = !petal.isDrifting;
          petal.targetRotationSpeed = (Math.random() - 0.5) * 0.04;
          // Add a gentle swoop
          petal.vx += (Math.random() - 0.5) * 0.6;
        }

        // Smooth rotation speed changes
        petal.rotationSpeed += (petal.targetRotationSpeed - petal.rotationSpeed) * 0.02;

        // Update 3D tilt (tumbling effect)
        petal.tilt += petal.tiltSpeed;
        // Vary tilt speed for organic movement
        petal.tiltSpeed += Math.sin(timeRef.current * 1.5 + petal.tiltPhase) * 0.0008;
        petal.tiltSpeed = Math.max(0.01, Math.min(0.04, petal.tiltSpeed));

        // Horizontal sway influenced by tilt
        const sway = Math.sin(timeRef.current * 0.6 + petal.swayPhase) * 0.35;
        const tiltInfluence = Math.sin(petal.tilt) * 0.25;

        // Apply velocities based on drift state
        if (petal.isDrifting) {
          // Drifting - more horizontal, slower fall
          petal.vx += sway * 0.06 + tiltInfluence * 0.08;
          petal.vy *= 0.985; // Slow down vertical
          petal.vy = Math.max(0.1, petal.vy); // Never stop falling completely
        } else {
          // Normal falling
          petal.vx += sway * 0.025;
          petal.vy += 0.002; // Gentle acceleration
          petal.vy = Math.min(0.7, petal.vy); // Terminal velocity
        }

        // Air resistance on horizontal movement
        petal.vx *= 0.988;

        // Apply movement
        petal.x += petal.vx;
        petal.y += petal.vy;

        // Rotation affected by horizontal movement and tilt
        petal.rotation += petal.rotationSpeed + petal.vx * 0.015;

        // Reset if off screen
        if (petal.y > canvas.height + 30 || petal.x < -40 || petal.x > canvas.width + 40) {
          petalsRef.current[index] = createPetal(canvas, true);
        }

        drawPetal(ctx, petal, darkMode, opacityMultiplier);
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
  }, [canvasRef, darkMode, opacity, createPetal, drawPetal, active]);
}
