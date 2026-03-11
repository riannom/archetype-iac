/**
 * Bitcoin Particles Animation
 * Rising Bitcoin symbols with glow effect
 */

import { useRef } from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

interface BitcoinParticle {
  x: number;
  y: number;
  size: number;
  rotation: number;
  rotationSpeed: number;
  vx: number;
  vy: number;
  opacity: number;
  fadeDirection: number;
}

export function useBitcoinParticles(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const particlesRef = useRef<BitcoinParticle[]>([]);

  function createParticle(canvas: HTMLCanvasElement): BitcoinParticle {
    return {
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      size: 12 + Math.random() * 20,
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.01,
      vx: (Math.random() - 0.5) * 0.2,
      vy: -0.2 - Math.random() * 0.3,
      opacity: Math.random() * 0.5,
      fadeDirection: 1,
    };
  }

  function drawBitcoin(
    ctx: CanvasRenderingContext2D,
    particle: BitcoinParticle,
    isDark: boolean,
    opacityMultiplier: number
  ) {
    ctx.save();
    ctx.translate(particle.x, particle.y);
    ctx.rotate(particle.rotation);

    const s = particle.size;
    const alpha = particle.opacity * opacityMultiplier;

    const baseColor = isDark
      ? { r: 247, g: 147, b: 26 }
      : { r: 242, g: 169, b: 0 };

    ctx.beginPath();
    ctx.arc(0, 0, s / 2, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${baseColor.r}, ${baseColor.g}, ${baseColor.b}, ${alpha * 0.3})`;
    ctx.fill();

    ctx.font = `bold ${s * 0.7}px Arial`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = `rgba(${baseColor.r}, ${baseColor.g}, ${baseColor.b}, ${alpha})`;
    ctx.fillText('\u20BF', 0, 0);

    ctx.shadowColor = `rgba(${baseColor.r}, ${baseColor.g}, ${baseColor.b}, ${alpha * 0.5})`;
    ctx.shadowBlur = 10;
    ctx.fillText('\u20BF', 0, 0);

    ctx.restore();
  }

  useCanvasAnimation(
    canvasRef,
    active,
    {
      onInit: (_ctx, canvas) => {
        const particleCount = Math.floor((canvas.width * canvas.height) / 50000);
        particlesRef.current = Array.from({ length: Math.max(8, particleCount) }, () =>
          createParticle(canvas)
        );
      },
      onFrame: (ctx, canvas) => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const opacityMultiplier = opacity / 50;

        particlesRef.current.forEach((particle, index) => {
          particle.x += particle.vx;
          particle.y += particle.vy;
          particle.rotation += particle.rotationSpeed;

          particle.opacity += particle.fadeDirection * 0.005;
          if (particle.opacity >= 0.6) particle.fadeDirection = -1;
          if (particle.opacity <= 0) {
            particlesRef.current[index] = createParticle(canvas);
            particlesRef.current[index].y = canvas.height + 20;
          }

          if (particle.y < -50) {
            particlesRef.current[index] = createParticle(canvas);
            particlesRef.current[index].y = canvas.height + 20;
          }

          drawBitcoin(ctx, particle, darkMode, opacityMultiplier);
        });
      },
    },
    [darkMode, opacity]
  );
}
