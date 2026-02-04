/**
 * Floating Shields Animation
 * Gentle floating protection shields
 */

import { useRef, useEffect, useCallback } from 'react';

interface Shield {
  x: number;
  y: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
  vx: number;
  vy: number;
  opacity: number;
  pulsePhase: number;
}

export function useFloatingShields(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const shieldsRef = useRef<Shield[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  const createShield = useCallback((canvas: HTMLCanvasElement): Shield => {
    return {
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      size: 20 + Math.random() * 30,
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.005,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      opacity: 0.3 + Math.random() * 0.3,
      pulsePhase: Math.random() * Math.PI * 2,
    };
  }, []);

  const drawShield = useCallback((
    ctx: CanvasRenderingContext2D,
    shield: Shield,
    isDark: boolean,
    opacityMultiplier: number,
    time: number
  ) => {
    ctx.save();
    ctx.translate(shield.x, shield.y);
    ctx.rotate(shield.rotation);

    const pulse = 1 + Math.sin(time * 2 + shield.pulsePhase) * 0.1;
    const s = shield.size * pulse;

    const baseColor = isDark
      ? { r: 218, g: 165, b: 32 }
      : { r: 184, g: 134, b: 11 };

    const alpha = shield.opacity * opacityMultiplier;

    ctx.beginPath();
    ctx.moveTo(0, -s * 0.6);
    ctx.bezierCurveTo(s * 0.5, -s * 0.5, s * 0.5, s * 0.2, 0, s * 0.6);
    ctx.bezierCurveTo(-s * 0.5, s * 0.2, -s * 0.5, -s * 0.5, 0, -s * 0.6);
    ctx.closePath();

    const gradient = ctx.createLinearGradient(0, -s * 0.6, 0, s * 0.6);
    gradient.addColorStop(0, `rgba(${baseColor.r + 40}, ${baseColor.g + 40}, ${baseColor.b + 20}, ${alpha})`);
    gradient.addColorStop(0.5, `rgba(${baseColor.r}, ${baseColor.g}, ${baseColor.b}, ${alpha})`);
    gradient.addColorStop(1, `rgba(${baseColor.r - 40}, ${baseColor.g - 40}, ${baseColor.b}, ${alpha * 0.8})`);
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.strokeStyle = `rgba(255, 255, 255, ${alpha * 0.5})`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, -s * 0.3);
    ctx.lineTo(0, s * 0.3);
    ctx.moveTo(-s * 0.2, 0);
    ctx.lineTo(s * 0.2, 0);
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

    const shieldCount = Math.floor((canvas.width * canvas.height) / 80000);
    shieldsRef.current = Array.from({ length: Math.max(5, shieldCount) }, () =>
      createShield(canvas)
    );

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      shieldsRef.current.forEach((shield) => {
        shield.x += shield.vx;
        shield.y += shield.vy;
        shield.rotation += shield.rotationSpeed;

        if (shield.x < -50) shield.x = canvas.width + 50;
        if (shield.x > canvas.width + 50) shield.x = -50;
        if (shield.y < -50) shield.y = canvas.height + 50;
        if (shield.y > canvas.height + 50) shield.y = -50;

        drawShield(ctx, shield, darkMode, opacityMultiplier, timeRef.current);
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
  }, [canvasRef, darkMode, opacity, createShield, drawShield, active]);
}
