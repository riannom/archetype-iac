/**
 * Falling Leaves Animation
 * Autumn leaves drifting and swaying as they fall
 */

import { useRef, useEffect, useCallback } from 'react';

interface Leaf {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
  targetRotationSpeed: number;
  tilt: number; // 3D tilt for fluttering effect
  tiltSpeed: number;
  tiltPhase: number;
  swayPhase: number;
  opacity: number;
  lobeCount: number; // 5 or 7 lobes for momiji
  color: { r: number; g: number; b: number };
  flutterTimer: number;
  isDrifting: boolean;
}

// Momiji autumn colors - vivid reds, oranges, and crimsons
const leafColors = [
  { r: 205, g: 55, b: 45 },   // Vivid red
  { r: 220, g: 85, b: 35 },   // Red-orange
  { r: 185, g: 45, b: 55 },   // Crimson
  { r: 230, g: 120, b: 30 },  // Orange
  { r: 200, g: 65, b: 50 },   // Deep red
  { r: 195, g: 40, b: 40 },   // Dark crimson
  { r: 240, g: 150, b: 40 },  // Golden orange
];

export function useFallingLeaves(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  _darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const leavesRef = useRef<Leaf[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  const createLeaf = useCallback((canvas: HTMLCanvasElement, startFromTop = true): Leaf => {
    const baseVy = 0.4 + Math.random() * 0.5;
    return {
      x: Math.random() * canvas.width,
      y: startFromTop ? -30 - Math.random() * 50 : Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3, // Slight initial horizontal drift
      vy: baseVy,
      size: 14 + Math.random() * 14,
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.02,
      targetRotationSpeed: (Math.random() - 0.5) * 0.04,
      tilt: Math.random() * Math.PI, // Initial 3D tilt
      tiltSpeed: 0.02 + Math.random() * 0.03,
      tiltPhase: Math.random() * Math.PI * 2,
      swayPhase: Math.random() * Math.PI * 2,
      opacity: 0.5 + Math.random() * 0.35,
      lobeCount: Math.random() > 0.5 ? 7 : 5, // Momiji have 5 or 7 lobes
      color: leafColors[Math.floor(Math.random() * leafColors.length)],
      flutterTimer: Math.random() * 100,
      isDrifting: Math.random() > 0.7, // Some leaves drift more sideways
    };
  }, []);

  const drawLeaf = useCallback((
    ctx: CanvasRenderingContext2D,
    leaf: Leaf,
    opacityMultiplier: number
  ) => {
    ctx.save();
    ctx.translate(leaf.x, leaf.y);
    ctx.rotate(leaf.rotation);

    // Apply 3D tilt effect - compresses width based on tilt angle
    const tiltFactor = Math.abs(Math.cos(leaf.tilt));
    ctx.scale(0.3 + tiltFactor * 0.7, 1);

    const s = leaf.size;
    const alpha = leaf.opacity * opacityMultiplier;
    const { r, g, b } = leaf.color;

    // Draw momiji (Japanese maple) leaf with proper palmate lobes
    const lobes = leaf.lobeCount;
    const angleStep = Math.PI / lobes;

    ctx.beginPath();

    // Start from center bottom (stem attachment point)
    ctx.moveTo(0, s * 0.15);

    for (let i = 0; i < lobes; i++) {
      // Calculate angles for this lobe
      const lobeAngle = -Math.PI / 2 + (i - (lobes - 1) / 2) * angleStep * 1.1;

      // Outer point of lobe (pointed tip)
      const lobeLength = s * (0.75 + (i === Math.floor(lobes / 2) ? 0.2 : 0)); // Center lobe is longer
      const tipX = Math.cos(lobeAngle) * lobeLength;
      const tipY = Math.sin(lobeAngle) * lobeLength;

      // Inner notch between lobes
      const notchAngle = lobeAngle + angleStep * 0.55;
      const notchLength = s * 0.25;
      const notchX = Math.cos(notchAngle) * notchLength;
      const notchY = Math.sin(notchAngle) * notchLength;

      // Control points for curved edges
      const cpOuterAngle = lobeAngle - angleStep * 0.3;
      const cpInnerAngle = lobeAngle + angleStep * 0.3;

      // Draw the lobe with bezier curves for natural serrated edge
      ctx.quadraticCurveTo(
        Math.cos(cpOuterAngle) * s * 0.45,
        Math.sin(cpOuterAngle) * s * 0.45,
        tipX,
        tipY
      );

      // If not last lobe, draw curve to notch
      if (i < lobes - 1) {
        ctx.quadraticCurveTo(
          Math.cos(cpInnerAngle) * s * 0.5,
          Math.sin(cpInnerAngle) * s * 0.5,
          notchX,
          notchY
        );
      }
    }

    // Close the path back to stem
    ctx.quadraticCurveTo(s * 0.15, -s * 0.1, 0, s * 0.15);
    ctx.closePath();

    // Fill with leaf color
    ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
    ctx.fill();

    // Draw leaf veins
    ctx.strokeStyle = `rgba(${Math.max(0, r - 40)}, ${Math.max(0, g - 30)}, ${Math.max(0, b - 20)}, ${alpha * 0.4})`;
    ctx.lineWidth = 0.8;

    // Main veins radiating from stem to each lobe tip
    for (let i = 0; i < lobes; i++) {
      const lobeAngle = -Math.PI / 2 + (i - (lobes - 1) / 2) * angleStep * 1.1;
      const lobeLength = s * (0.55 + (i === Math.floor(lobes / 2) ? 0.15 : 0));
      const tipX = Math.cos(lobeAngle) * lobeLength;
      const tipY = Math.sin(lobeAngle) * lobeLength;

      ctx.beginPath();
      ctx.moveTo(0, s * 0.1);
      ctx.lineTo(tipX, tipY);
      ctx.stroke();
    }

    // Draw small stem
    ctx.strokeStyle = `rgba(${Math.max(0, r - 60)}, ${Math.max(0, g - 50)}, ${Math.max(0, b - 40)}, ${alpha * 0.7})`;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(0, s * 0.15);
    ctx.lineTo(0, s * 0.35);
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

    const leafCount = Math.floor((canvas.width * canvas.height) / 30000);
    leavesRef.current = Array.from({ length: Math.max(12, leafCount) }, () =>
      createLeaf(canvas, false)
    );

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      leavesRef.current.forEach((leaf, index) => {
        // Update flutter timer
        leaf.flutterTimer += 1;

        // Occasionally change drift direction (simulates catching air)
        if (leaf.flutterTimer > 80 + Math.random() * 60) {
          leaf.flutterTimer = 0;
          leaf.isDrifting = !leaf.isDrifting;
          leaf.targetRotationSpeed = (Math.random() - 0.5) * 0.06;
          // Add a swoop effect
          leaf.vx += (Math.random() - 0.5) * 0.8;
        }

        // Smooth rotation speed changes
        leaf.rotationSpeed += (leaf.targetRotationSpeed - leaf.rotationSpeed) * 0.02;

        // Update tilt (3D flutter effect)
        leaf.tilt += leaf.tiltSpeed;
        // Vary tilt speed for organic movement
        leaf.tiltSpeed += (Math.sin(timeRef.current * 2 + leaf.tiltPhase) * 0.001);
        leaf.tiltSpeed = Math.max(0.01, Math.min(0.05, leaf.tiltSpeed));

        // Horizontal sway - influenced by tilt
        const sway = Math.sin(timeRef.current * 0.8 + leaf.swayPhase) * 0.4;
        const tiltInfluence = Math.sin(leaf.tilt) * 0.3;

        // Apply velocities with sway
        if (leaf.isDrifting) {
          // Drifting leaves move more sideways
          leaf.vx += sway * 0.08 + tiltInfluence * 0.1;
          leaf.vy *= 0.98; // Slow vertical when catching air
          leaf.vy = Math.max(0.15, leaf.vy); // But never stop falling
        } else {
          // Normal falling
          leaf.vx += sway * 0.03;
          leaf.vy += 0.003; // Gentle acceleration
          leaf.vy = Math.min(0.9, leaf.vy); // Terminal velocity
        }

        // Air resistance on horizontal movement
        leaf.vx *= 0.985;

        // Apply movement
        leaf.x += leaf.vx;
        leaf.y += leaf.vy;

        // Rotation affected by horizontal movement
        leaf.rotation += leaf.rotationSpeed + leaf.vx * 0.02;

        // Reset if off screen
        if (leaf.y > canvas.height + 50 || leaf.x < -50 || leaf.x > canvas.width + 50) {
          leavesRef.current[index] = createLeaf(canvas, true);
        }

        drawLeaf(ctx, leaf, opacityMultiplier);
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
  }, [canvasRef, opacity, createLeaf, drawLeaf, active]);
}
