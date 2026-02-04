/**
 * Hash Storm Animation
 * Cryptographic characters radiating outward from a central core.
 * Hexadecimal characters expand and fade as they travel outward.
 */

import { useEffect, useRef } from 'react';

interface HashChar {
  x: number;
  y: number;
  char: string;
  angle: number;
  radius: number;
  speed: number;
  angularSpeed: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
}

export function useHashStorm(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const charsRef = useRef<HashChar[]>([]);
  const animationRef = useRef<number>(0);
  const timeRef = useRef<number>(0);
  const centerRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  const maxRadiusRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const hexChars = '0123456789abcdef';
    const hashPrefixes = ['0x', 'SHA', '256', 'BTC', '###'];

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      // Center positioned to the right side of the screen
      centerRef.current = {
        x: canvas.width * 0.75,
        y: canvas.height / 2,
      };
      // Max radius is the distance to the corners
      maxRadiusRef.current = Math.sqrt(
        Math.pow(canvas.width / 2, 2) + Math.pow(canvas.height / 2, 2)
      ) * 1.2;
      initializeChars();
    };

    const initializeChars = () => {
      charsRef.current = [];
      // Start with characters at various radii
      const charCount = Math.floor((canvas.width * canvas.height) / 6000);
      for (let i = 0; i < charCount; i++) {
        charsRef.current.push(createChar(Math.random() * maxRadiusRef.current * 0.8));
      }
    };

    const createChar = (startRadius = 0): HashChar => {
      const angle = Math.random() * Math.PI * 2;

      // Mix of hex chars and occasional hash-related strings
      let char: string;
      if (Math.random() < 0.85) {
        char = hexChars[Math.floor(Math.random() * hexChars.length)];
      } else {
        char = hashPrefixes[Math.floor(Math.random() * hashPrefixes.length)];
      }

      return {
        x: centerRef.current.x,
        y: centerRef.current.y,
        char,
        angle,
        radius: startRadius,
        speed: 0.3 + Math.random() * 0.5, // Outward speed
        angularSpeed: (Math.random() - 0.5) * 0.01, // Slight spiral
        size: 10 + Math.random() * 14,
        rotation: Math.random() * Math.PI * 2,
        rotationSpeed: (Math.random() - 0.5) * 0.03,
      };
    };

    const drawChar = (hashChar: HashChar, opacityMult: number) => {
      const maxRadius = maxRadiusRef.current;
      // Fade out as it goes further from center
      const distanceRatio = hashChar.radius / maxRadius;
      const alpha = Math.max(0, (1 - distanceRatio) * 0.8) * opacityMult;

      if (alpha <= 0) return;

      ctx.save();
      ctx.translate(hashChar.x, hashChar.y);
      ctx.rotate(hashChar.rotation);

      // Glow effect for chars closer to center
      const glowIntensity = Math.max(0, 1 - distanceRatio * 1.5);

      if (glowIntensity > 0.2) {
        const glowColor = darkMode
          ? `rgba(0, 255, 150, ${alpha * glowIntensity * 0.6})`
          : `rgba(0, 200, 100, ${alpha * glowIntensity * 0.6})`;

        ctx.shadowColor = glowColor;
        ctx.shadowBlur = 15 * glowIntensity;
      }

      // Character color - brighter near center
      const brightness = 1 - distanceRatio * 0.5;
      const baseColor = darkMode
        ? { r: 0, g: Math.floor(180 + 75 * brightness), b: Math.floor(80 + 120 * brightness) }
        : { r: 0, g: Math.floor(120 + 80 * brightness), b: Math.floor(60 + 90 * brightness) };

      ctx.fillStyle = `rgba(${baseColor.r}, ${baseColor.g}, ${baseColor.b}, ${alpha})`;
      ctx.font = `${hashChar.size}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(hashChar.char, 0, 0);

      ctx.restore();
    };

    const drawCore = (opacityMult: number, time: number) => {
      const cx = centerRef.current.x;
      const cy = centerRef.current.y;
      const coreRadius = 40;

      // Pulsing core
      const pulse = Math.sin(time * 0.004) * 0.15 + 0.85;

      // Core glow - larger radius
      const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreRadius * 4 * pulse);
      const coreColor = darkMode
        ? { r: 0, g: 255, b: 150 }
        : { r: 0, g: 200, b: 100 };

      gradient.addColorStop(0, `rgba(${coreColor.r}, ${coreColor.g}, ${coreColor.b}, ${0.9 * opacityMult})`);
      gradient.addColorStop(0.2, `rgba(${coreColor.r}, ${coreColor.g}, ${coreColor.b}, ${0.5 * opacityMult})`);
      gradient.addColorStop(0.5, `rgba(${coreColor.r}, ${coreColor.g}, ${coreColor.b}, ${0.15 * opacityMult})`);
      gradient.addColorStop(1, 'rgba(0, 255, 150, 0)');

      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.arc(cx, cy, coreRadius * 4 * pulse, 0, Math.PI * 2);
      ctx.fill();

      // Inner bright core
      ctx.fillStyle = `rgba(255, 255, 255, ${0.95 * opacityMult})`;
      ctx.beginPath();
      ctx.arc(cx, cy, coreRadius * 0.25 * pulse, 0, Math.PI * 2);
      ctx.fill();

      // Rotating hash ring
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(time * 0.0015);

      const ringText = 'SHA256•BTC•HASH•';
      ctx.font = '11px monospace';

      for (let i = 0; i < ringText.length; i++) {
        const charAngle = (i / ringText.length) * Math.PI * 2;
        const charX = Math.cos(charAngle) * coreRadius;
        const charY = Math.sin(charAngle) * coreRadius;

        ctx.save();
        ctx.translate(charX, charY);
        ctx.rotate(charAngle + Math.PI / 2);
        ctx.fillStyle = `rgba(${coreColor.r}, ${coreColor.g}, ${coreColor.b}, ${0.7 * opacityMult})`;
        ctx.fillText(ringText[i], 0, 0);
        ctx.restore();
      }

      ctx.restore();
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const opacityMult = opacity / 50;

      // Spawn new characters from center
      if (Math.random() < 0.15) {
        charsRef.current.push(createChar(20 + Math.random() * 30));
      }

      // Update and draw characters
      charsRef.current = charsRef.current.filter((hashChar) => {
        // Move outward
        hashChar.radius += hashChar.speed;

        // Slight spiral
        hashChar.angle += hashChar.angularSpeed;

        // Calculate position
        hashChar.x = centerRef.current.x + Math.cos(hashChar.angle) * hashChar.radius;
        hashChar.y = centerRef.current.y + Math.sin(hashChar.angle) * hashChar.radius;

        // Self rotation
        hashChar.rotation += hashChar.rotationSpeed;

        // Remove if too far out
        if (hashChar.radius > maxRadiusRef.current) {
          return false;
        }

        drawChar(hashChar, opacityMult);
        return true;
      });

      // Draw core on top
      drawCore(opacityMult, time);

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
