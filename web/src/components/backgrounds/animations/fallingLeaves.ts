/**
 * Falling Leaves Animation
 * Autumn leaves drifting and swaying as they fall
 */

import { useRef, useCallback } from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

interface Leaf {
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
  lobeCount: number;
  color: { r: number; g: number; b: number };
  flutterTimer: number;
  isDrifting: boolean;
}

// Momiji autumn colors - vivid reds, oranges, and crimsons
const leafColors = [
  { r: 205, g: 55, b: 45 },
  { r: 220, g: 85, b: 35 },
  { r: 185, g: 45, b: 55 },
  { r: 230, g: 120, b: 30 },
  { r: 200, g: 65, b: 50 },
  { r: 195, g: 40, b: 40 },
  { r: 240, g: 150, b: 40 },
];

export function useFallingLeaves(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  _darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const leavesRef = useRef<Leaf[]>([]);

  const createLeaf = useCallback((canvas: HTMLCanvasElement, startFromTop = true): Leaf => {
    const baseVy = 0.4 + Math.random() * 0.5;
    return {
      x: Math.random() * canvas.width,
      y: startFromTop ? -30 - Math.random() * 50 : Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: baseVy,
      size: 14 + Math.random() * 14,
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.02,
      targetRotationSpeed: (Math.random() - 0.5) * 0.04,
      tilt: Math.random() * Math.PI,
      tiltSpeed: 0.02 + Math.random() * 0.03,
      tiltPhase: Math.random() * Math.PI * 2,
      swayPhase: Math.random() * Math.PI * 2,
      opacity: 0.5 + Math.random() * 0.35,
      lobeCount: Math.random() > 0.5 ? 7 : 5,
      color: leafColors[Math.floor(Math.random() * leafColors.length)],
      flutterTimer: Math.random() * 100,
      isDrifting: Math.random() > 0.7,
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

    const tiltFactor = Math.abs(Math.cos(leaf.tilt));
    ctx.scale(0.3 + tiltFactor * 0.7, 1);

    const s = leaf.size;
    const alpha = leaf.opacity * opacityMultiplier;
    const { r, g, b } = leaf.color;

    const lobes = leaf.lobeCount;
    const angleStep = Math.PI / lobes;

    ctx.beginPath();
    ctx.moveTo(0, s * 0.15);

    for (let i = 0; i < lobes; i++) {
      const lobeAngle = -Math.PI / 2 + (i - (lobes - 1) / 2) * angleStep * 1.1;
      const lobeLength = s * (0.75 + (i === Math.floor(lobes / 2) ? 0.2 : 0));
      const tipX = Math.cos(lobeAngle) * lobeLength;
      const tipY = Math.sin(lobeAngle) * lobeLength;

      const notchAngle = lobeAngle + angleStep * 0.55;
      const notchLength = s * 0.25;
      const notchX = Math.cos(notchAngle) * notchLength;
      const notchY = Math.sin(notchAngle) * notchLength;

      const cpOuterAngle = lobeAngle - angleStep * 0.3;
      const cpInnerAngle = lobeAngle + angleStep * 0.3;

      ctx.quadraticCurveTo(
        Math.cos(cpOuterAngle) * s * 0.45,
        Math.sin(cpOuterAngle) * s * 0.45,
        tipX,
        tipY
      );

      if (i < lobes - 1) {
        ctx.quadraticCurveTo(
          Math.cos(cpInnerAngle) * s * 0.5,
          Math.sin(cpInnerAngle) * s * 0.5,
          notchX,
          notchY
        );
      }
    }

    ctx.quadraticCurveTo(s * 0.15, -s * 0.1, 0, s * 0.15);
    ctx.closePath();

    ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
    ctx.fill();

    ctx.strokeStyle = `rgba(${Math.max(0, r - 40)}, ${Math.max(0, g - 30)}, ${Math.max(0, b - 20)}, ${alpha * 0.4})`;
    ctx.lineWidth = 0.8;

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

    ctx.strokeStyle = `rgba(${Math.max(0, r - 60)}, ${Math.max(0, g - 50)}, ${Math.max(0, b - 40)}, ${alpha * 0.7})`;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(0, s * 0.15);
    ctx.lineTo(0, s * 0.35);
    ctx.stroke();

    ctx.restore();
  }, []);

  useCanvasAnimation(canvasRef, _darkMode, opacity, active, {
    init: (_ctx, canvas) => {
      const leafCount = Math.floor((canvas.width * canvas.height) / 30000);
      leavesRef.current = Array.from({ length: Math.max(12, leafCount) }, () =>
        createLeaf(canvas, false)
      );
    },
    draw: (ctx, canvas, time) => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const opacityMultiplier = opacity / 50;

      leavesRef.current.forEach((leaf, index) => {
        leaf.flutterTimer += 1;

        if (leaf.flutterTimer > 80 + Math.random() * 60) {
          leaf.flutterTimer = 0;
          leaf.isDrifting = !leaf.isDrifting;
          leaf.targetRotationSpeed = (Math.random() - 0.5) * 0.06;
          leaf.vx += (Math.random() - 0.5) * 0.8;
        }

        leaf.rotationSpeed += (leaf.targetRotationSpeed - leaf.rotationSpeed) * 0.02;
        leaf.tilt += leaf.tiltSpeed;
        leaf.tiltSpeed += (Math.sin(time * 2 + leaf.tiltPhase) * 0.001);
        leaf.tiltSpeed = Math.max(0.01, Math.min(0.05, leaf.tiltSpeed));

        const sway = Math.sin(time * 0.8 + leaf.swayPhase) * 0.4;
        const tiltInfluence = Math.sin(leaf.tilt) * 0.3;

        if (leaf.isDrifting) {
          leaf.vx += sway * 0.08 + tiltInfluence * 0.1;
          leaf.vy *= 0.98;
          leaf.vy = Math.max(0.15, leaf.vy);
        } else {
          leaf.vx += sway * 0.03;
          leaf.vy += 0.003;
          leaf.vy = Math.min(0.9, leaf.vy);
        }

        leaf.vx *= 0.985;
        leaf.x += leaf.vx;
        leaf.y += leaf.vy;
        leaf.rotation += leaf.rotationSpeed + leaf.vx * 0.02;

        if (leaf.y > canvas.height + 50 || leaf.x < -50 || leaf.x > canvas.width + 50) {
          leavesRef.current[index] = createLeaf(canvas, true);
        }

        drawLeaf(ctx, leaf, opacityMultiplier);
      });
    },
  });
}
