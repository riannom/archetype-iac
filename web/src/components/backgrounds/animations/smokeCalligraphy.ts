/**
 * Smoke Calligraphy Animation
 *
 * Delicate wisps of incense smoke that curl, twist, and dissipate.
 * The trails occasionally form brush-stroke-like shapes before dissolving.
 */

import { useEffect, useRef } from 'react';

interface SmokeParticle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  opacity: number;
  life: number;
  maxLife: number;
  turbulencePhase: number;
  turbulenceSpeed: number;
}

interface SmokeSource {
  x: number;
  y: number;
  emitRate: number;
  emitTimer: number;
  swayPhase: number;
  active: boolean;
  lifetime: number;
}

export function useSmokeCalligraphy(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const particlesRef = useRef<SmokeParticle[]>([]);
  const sourcesRef = useRef<SmokeSource[]>([]);
  const animationRef = useRef<number>(0);
  const timeRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeSources();
    };

    const initializeSources = () => {
      const { width, height } = canvas;
      particlesRef.current = [];
      sourcesRef.current = [];

      // Create 2-3 smoke sources at bottom of screen
      const sourceCount = 2 + Math.floor(Math.random() * 2);
      for (let i = 0; i < sourceCount; i++) {
        sourcesRef.current.push({
          x: width * 0.2 + (i / (sourceCount - 1 || 1)) * width * 0.6,
          y: height * 0.9,
          emitRate: 30 + Math.random() * 20,
          emitTimer: 0,
          swayPhase: Math.random() * Math.PI * 2,
          active: true,
          lifetime: 10000 + Math.random() * 20000,
        });
      }
    };

    const createParticle = (source: SmokeSource): SmokeParticle => {
      const sway = Math.sin(source.swayPhase) * 2;
      return {
        x: source.x + sway + (Math.random() - 0.5) * 4,
        y: source.y,
        vx: (Math.random() - 0.5) * 0.3,
        vy: -0.8 - Math.random() * 0.4,
        size: 15 + Math.random() * 20,
        opacity: 0.4 + Math.random() * 0.2,
        life: 0,
        maxLife: 400 + Math.random() * 200,
        turbulencePhase: Math.random() * Math.PI * 2,
        turbulenceSpeed: 0.02 + Math.random() * 0.02,
      };
    };

    const drawSmoke = () => {
      const { width, height } = canvas;
      const opacityMult = opacity / 50;

      // Draw each particle
      particlesRef.current.forEach((p) => {
        const lifeRatio = p.life / p.maxLife;
        const fadeIn = Math.min(1, p.life / 30);
        const fadeOut = 1 - Math.pow(lifeRatio, 2);
        const alpha = p.opacity * fadeIn * fadeOut * opacityMult;

        if (alpha <= 0) return;

        // Size grows as smoke rises and disperses
        const currentSize = p.size * (1 + lifeRatio * 2);

        // Create soft gradient for each smoke puff
        const gradient = ctx.createRadialGradient(
          p.x, p.y, 0,
          p.x, p.y, currentSize
        );

        if (darkMode) {
          gradient.addColorStop(0, `rgba(180, 175, 170, ${alpha * 0.6})`);
          gradient.addColorStop(0.4, `rgba(150, 145, 140, ${alpha * 0.3})`);
          gradient.addColorStop(1, `rgba(120, 115, 110, 0)`);
        } else {
          gradient.addColorStop(0, `rgba(100, 95, 90, ${alpha * 0.5})`);
          gradient.addColorStop(0.4, `rgba(130, 125, 120, ${alpha * 0.25})`);
          gradient.addColorStop(1, `rgba(160, 155, 150, 0)`);
        }

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(p.x, p.y, currentSize, 0, Math.PI * 2);
        ctx.fill();
      });
    };

    const animate = () => {
      const { width, height } = canvas;
      timeRef.current += 16;

      // Clear with subtle fade
      ctx.fillStyle = darkMode ? 'rgba(20, 20, 25, 0.08)' : 'rgba(250, 248, 245, 0.08)';
      ctx.fillRect(0, 0, width, height);

      // Update and emit from sources
      sourcesRef.current.forEach((source) => {
        source.swayPhase += 0.015;
        source.lifetime -= 16;
        source.emitTimer -= 16;

        // Emit particles
        if (source.active && source.emitTimer <= 0) {
          particlesRef.current.push(createParticle(source));
          source.emitTimer = source.emitRate;
        }

        // Deactivate old sources, create new ones
        if (source.lifetime <= 0) {
          source.active = false;
          // Respawn after a delay
          setTimeout(() => {
            source.x = width * 0.15 + Math.random() * width * 0.7;
            source.lifetime = 10000 + Math.random() * 20000;
            source.active = true;
          }, 3000 + Math.random() * 5000);
          source.lifetime = Infinity; // Prevent multiple respawns
        }
      });

      // Update particles
      particlesRef.current = particlesRef.current.filter((p) => {
        p.life += 1;

        // Turbulent motion - creates curling effect
        p.turbulencePhase += p.turbulenceSpeed;
        const turbulenceX = Math.sin(p.turbulencePhase) * 0.15;
        const turbulenceY = Math.cos(p.turbulencePhase * 0.7) * 0.05;

        // Global wind drift
        const windX = Math.sin(timeRef.current * 0.0003 + p.y * 0.003) * 0.08;

        p.vx += turbulenceX + windX;
        p.vy += turbulenceY;

        // Damping
        p.vx *= 0.98;
        p.vy *= 0.995;

        // Rising slows down over time
        p.vy = Math.max(p.vy, -1.2);

        p.x += p.vx;
        p.y += p.vy;

        return p.life < p.maxLife && p.y > -50;
      });

      drawSmoke();

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
