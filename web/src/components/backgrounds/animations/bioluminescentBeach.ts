/**
 * Bioluminescent Beach Animation
 *
 * Waves lapping shore with glowing blue bioluminescence,
 * stars reflecting on water, peaceful nighttime beach scene.
 */

import { useEffect, useRef } from 'react';

interface Wave {
  x: number;
  y: number;
  width: number;
  height: number;
  phase: number;
  speed: number;
  glowIntensity: number;
}

interface BioParticle {
  x: number;
  y: number;
  size: number;
  brightness: number;
  fadeSpeed: number;
  driftX: number;
  driftY: number;
  life: number;
  maxLife: number;
}

interface Star {
  x: number;
  y: number;
  size: number;
  twinklePhase: number;
  twinkleSpeed: number;
}

interface Reflection {
  x: number;
  y: number;
  width: number;
  brightness: number;
  wobblePhase: number;
}

export function useBioluminescentBeach(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  enabled: boolean
) {
  const wavesRef = useRef<Wave[]>([]);
  const particlesRef = useRef<BioParticle[]>([]);
  const starsRef = useRef<Star[]>([]);
  const reflectionsRef = useRef<Reflection[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;

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

    const width = canvas.width;
    const height = canvas.height;
    const shoreY = height * 0.65; // Where beach meets water

    // Initialize stars
    starsRef.current = [];
    for (let i = 0; i < 80; i++) {
      starsRef.current.push({
        x: Math.random() * width,
        y: Math.random() * shoreY * 0.6, // Stars in sky only
        size: 0.5 + Math.random() * 1.5,
        twinklePhase: Math.random() * Math.PI * 2,
        twinkleSpeed: 0.02 + Math.random() * 0.03,
      });
    }

    // Initialize waves
    wavesRef.current = [];
    for (let i = 0; i < 6; i++) {
      wavesRef.current.push({
        x: 0,
        y: shoreY + i * 15,
        width: width,
        height: 8 + Math.random() * 6,
        phase: Math.random() * Math.PI * 2,
        speed: 0.015 + Math.random() * 0.01,
        glowIntensity: 0.3 + Math.random() * 0.4,
      });
    }

    // Initialize star reflections on water
    reflectionsRef.current = [];
    for (let i = 0; i < 15; i++) {
      reflectionsRef.current.push({
        x: Math.random() * width,
        y: shoreY + 30 + Math.random() * (height - shoreY - 50),
        width: 2 + Math.random() * 4,
        brightness: 0.2 + Math.random() * 0.3,
        wobblePhase: Math.random() * Math.PI * 2,
      });
    }

    particlesRef.current = [];

    const animate = () => {
      const currentWidth = canvas.width;
      const currentHeight = canvas.height;
      ctx.clearRect(0, 0, currentWidth, currentHeight);
      timeRef.current += 0.016;

      // Sky gradient (dark night sky)
      const skyGradient = ctx.createLinearGradient(0, 0, 0, shoreY);
      if (darkMode) {
        skyGradient.addColorStop(0, '#0a0a1a');
        skyGradient.addColorStop(0.5, '#0d1020');
        skyGradient.addColorStop(1, '#101428');
      } else {
        skyGradient.addColorStop(0, '#1a1a2e');
        skyGradient.addColorStop(0.5, '#16213e');
        skyGradient.addColorStop(1, '#1a1a3a');
      }
      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, currentWidth, shoreY);

      // Draw stars
      starsRef.current.forEach((star) => {
        star.twinklePhase += star.twinkleSpeed;
        const twinkle = 0.5 + Math.sin(star.twinklePhase) * 0.5;

        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size * twinkle, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${0.4 + twinkle * 0.6})`;
        ctx.fill();
      });

      // Ocean gradient
      const oceanGradient = ctx.createLinearGradient(0, shoreY, 0, currentHeight);
      if (darkMode) {
        oceanGradient.addColorStop(0, '#0a1628');
        oceanGradient.addColorStop(0.5, '#081420');
        oceanGradient.addColorStop(1, '#060e18');
      } else {
        oceanGradient.addColorStop(0, '#0d1e30');
        oceanGradient.addColorStop(0.5, '#0a1828');
        oceanGradient.addColorStop(1, '#081420');
      }
      ctx.fillStyle = oceanGradient;
      ctx.fillRect(0, shoreY, currentWidth, currentHeight - shoreY);

      // Draw star reflections on water
      reflectionsRef.current.forEach((ref) => {
        ref.wobblePhase += 0.03;
        const wobble = Math.sin(ref.wobblePhase) * 3;

        ctx.beginPath();
        ctx.ellipse(
          ref.x + wobble,
          ref.y,
          ref.width,
          ref.width * 0.3,
          0,
          0,
          Math.PI * 2
        );
        ctx.fillStyle = `rgba(255, 255, 255, ${ref.brightness * 0.3})`;
        ctx.fill();
      });

      // Draw waves with bioluminescence
      wavesRef.current.forEach((wave) => {
        wave.phase += wave.speed;

        ctx.beginPath();
        ctx.moveTo(0, wave.y);

        // Create wave curve
        for (let x = 0; x <= currentWidth; x += 5) {
          const waveHeight =
            Math.sin(x * 0.02 + wave.phase) * wave.height +
            Math.sin(x * 0.01 + wave.phase * 0.5) * wave.height * 0.5;
          ctx.lineTo(x, wave.y + waveHeight);
        }

        ctx.lineTo(currentWidth, wave.y + 20);
        ctx.lineTo(0, wave.y + 20);
        ctx.closePath();

        // Bioluminescent glow
        const glowGradient = ctx.createLinearGradient(0, wave.y - 5, 0, wave.y + 15);
        const glowPulse = 0.7 + Math.sin(timeRef.current * 2 + wave.phase) * 0.3;
        const intensity = wave.glowIntensity * glowPulse;

        if (darkMode) {
          glowGradient.addColorStop(0, `rgba(0, 180, 255, ${intensity * 0.4})`);
          glowGradient.addColorStop(0.5, `rgba(0, 220, 255, ${intensity * 0.2})`);
          glowGradient.addColorStop(1, 'rgba(0, 150, 200, 0)');
        } else {
          glowGradient.addColorStop(0, `rgba(50, 200, 255, ${intensity * 0.5})`);
          glowGradient.addColorStop(0.5, `rgba(30, 220, 255, ${intensity * 0.25})`);
          glowGradient.addColorStop(1, 'rgba(20, 160, 220, 0)');
        }

        ctx.fillStyle = glowGradient;
        ctx.fill();

        // Spawn bio particles along wave crest
        if (Math.random() < 0.15) {
          const spawnX = Math.random() * currentWidth;
          const waveY =
            wave.y +
            Math.sin(spawnX * 0.02 + wave.phase) * wave.height +
            Math.sin(spawnX * 0.01 + wave.phase * 0.5) * wave.height * 0.5;

          particlesRef.current.push({
            x: spawnX,
            y: waveY,
            size: 1 + Math.random() * 2,
            brightness: 0.5 + Math.random() * 0.5,
            fadeSpeed: 0.01 + Math.random() * 0.02,
            driftX: (Math.random() - 0.5) * 0.5,
            driftY: Math.random() * 0.3,
            life: 1,
            maxLife: 1,
          });
        }
      });

      // Update and draw bio particles
      particlesRef.current = particlesRef.current.filter((p) => {
        p.x += p.driftX;
        p.y += p.driftY;
        p.life -= p.fadeSpeed;

        if (p.life <= 0) return false;

        const alpha = p.life * p.brightness;
        const glowRadius = p.size * 3;

        // Outer glow
        const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowRadius);
        glow.addColorStop(0, `rgba(0, 220, 255, ${alpha * 0.8})`);
        glow.addColorStop(0.5, `rgba(0, 180, 255, ${alpha * 0.3})`);
        glow.addColorStop(1, 'rgba(0, 150, 200, 0)');

        ctx.beginPath();
        ctx.arc(p.x, p.y, glowRadius, 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();

        // Core
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(150, 255, 255, ${alpha})`;
        ctx.fill();

        return true;
      });

      // Limit particles
      if (particlesRef.current.length > 200) {
        particlesRef.current = particlesRef.current.slice(-150);
      }

      // Beach/shore (sandy area)
      const beachGradient = ctx.createLinearGradient(0, shoreY - 20, 0, shoreY + 30);
      if (darkMode) {
        beachGradient.addColorStop(0, '#1a1510');
        beachGradient.addColorStop(0.5, '#1f1a14');
        beachGradient.addColorStop(1, 'transparent');
      } else {
        beachGradient.addColorStop(0, '#2a2520');
        beachGradient.addColorStop(0.5, '#252018');
        beachGradient.addColorStop(1, 'transparent');
      }

      ctx.fillStyle = beachGradient;
      ctx.fillRect(0, shoreY - 20, currentWidth, 50);

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [canvasRef, darkMode, opacity, enabled]);
}
