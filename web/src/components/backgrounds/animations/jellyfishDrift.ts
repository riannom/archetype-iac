/**
 * Jellyfish Drift Animation
 *
 * Graceful bioluminescent jellyfish floating through deep ocean.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface Jellyfish {
  x: number;
  y: number;
  size: number;
  hue: number;
  pulsePhase: number;
  pulseSpeed: number;
  driftSpeed: number;
  wobblePhase: number;
  tentaclePhases: number[];
  glowIntensity: number;
}

interface Particle {
  x: number;
  y: number;
  size: number;
  opacity: number;
  speed: number;
  hue: number;
}

export function useJellyfishDrift(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const jellyfishRef = useRef<Jellyfish[]>([]);
  const particlesRef = useRef<Particle[]>([]);
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
      initializeElements();
    };

    const initializeElements = () => {
      const width = canvas.width;
      const height = canvas.height;

      // Create jellyfish
      jellyfishRef.current = [];
      const count = Math.floor(width / 300) + 2;

      for (let i = 0; i < count; i++) {
        jellyfishRef.current.push(createJellyfish(width, height));
      }

      // Create floating particles (plankton)
      particlesRef.current = [];
      for (let i = 0; i < 50; i++) {
        particlesRef.current.push({
          x: Math.random() * width,
          y: Math.random() * height,
          size: 1 + Math.random() * 2,
          opacity: 0.2 + Math.random() * 0.4,
          speed: 0.1 + Math.random() * 0.2,
          hue: 180 + Math.random() * 60,
        });
      }
    };

    const createJellyfish = (width: number, height: number): Jellyfish => {
      const tentacleCount = 6 + Math.floor(Math.random() * 4);
      const tentaclePhases: number[] = [];
      for (let t = 0; t < tentacleCount; t++) {
        tentaclePhases.push(Math.random() * Math.PI * 2);
      }

      return {
        x: Math.random() * width,
        y: Math.random() * height,
        size: 30 + Math.random() * 40,
        hue: 180 + Math.random() * 80, // Cyan to purple
        pulsePhase: Math.random() * Math.PI * 2,
        pulseSpeed: 0.02 + Math.random() * 0.01,
        driftSpeed: 0.2 + Math.random() * 0.3,
        wobblePhase: Math.random() * Math.PI * 2,
        tentaclePhases,
        glowIntensity: 0.3 + Math.random() * 0.4,
      };
    };

    const drawBackground = () => {
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
      if (darkMode) {
        gradient.addColorStop(0, '#000510');
        gradient.addColorStop(0.3, '#001020');
        gradient.addColorStop(0.7, '#001530');
        gradient.addColorStop(1, '#000815');
      } else {
        gradient.addColorStop(0, '#102040');
        gradient.addColorStop(0.3, '#153050');
        gradient.addColorStop(0.7, '#204060');
        gradient.addColorStop(1, '#152535');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    };

    const drawParticle = (particle: Particle, time: number) => {
      particle.y -= particle.speed;
      particle.x += Math.sin(time * 0.001 + particle.y * 0.01) * 0.2;

      if (particle.y < -10) {
        particle.y = canvas.height + 10;
        particle.x = Math.random() * canvas.width;
      }

      const twinkle = Math.sin(time * 0.003 + particle.x) * 0.3 + 0.7;
      ctx.beginPath();
      ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${particle.hue}, 80%, 70%, ${particle.opacity * twinkle})`;
      ctx.fill();
    };

    const drawJellyfish = (jelly: Jellyfish, time: number) => {
      // Update position
      jelly.y -= jelly.driftSpeed;
      jelly.wobblePhase += 0.01;
      jelly.x += Math.sin(jelly.wobblePhase) * 0.5;
      jelly.pulsePhase += jelly.pulseSpeed;

      // Reset if off screen
      if (jelly.y < -jelly.size * 3) {
        jelly.y = canvas.height + jelly.size * 2;
        jelly.x = Math.random() * canvas.width;
      }

      const pulse = Math.sin(jelly.pulsePhase) * 0.15 + 0.85;
      const bellWidth = jelly.size * pulse;
      const bellHeight = jelly.size * 0.7 * (1.1 - pulse * 0.1);

      ctx.save();
      ctx.translate(jelly.x, jelly.y);

      // Glow effect
      const glowGradient = ctx.createRadialGradient(0, 0, 0, 0, 0, jelly.size * 1.5);
      glowGradient.addColorStop(0, `hsla(${jelly.hue}, 80%, 60%, ${jelly.glowIntensity * 0.3})`);
      glowGradient.addColorStop(0.5, `hsla(${jelly.hue}, 70%, 50%, ${jelly.glowIntensity * 0.1})`);
      glowGradient.addColorStop(1, 'transparent');
      ctx.fillStyle = glowGradient;
      ctx.beginPath();
      ctx.arc(0, 0, jelly.size * 1.5, 0, Math.PI * 2);
      ctx.fill();

      // Tentacles (draw first, behind bell)
      const tentacleCount = jelly.tentaclePhases.length;
      for (let t = 0; t < tentacleCount; t++) {
        const baseX = ((t - tentacleCount / 2 + 0.5) / tentacleCount) * bellWidth * 1.5;
        jelly.tentaclePhases[t] += 0.02;
        const tentaclePhase = jelly.tentaclePhases[t];

        ctx.beginPath();
        ctx.moveTo(baseX, bellHeight * 0.3);

        // Draw wavy tentacle
        const tentacleLength = jelly.size * (1.5 + Math.sin(tentaclePhase) * 0.3);
        for (let i = 0; i <= 10; i++) {
          const progress = i / 10;
          const wave = Math.sin(tentaclePhase + progress * 4) * 8 * progress;
          ctx.lineTo(baseX + wave, bellHeight * 0.3 + tentacleLength * progress);
        }

        ctx.strokeStyle = `hsla(${jelly.hue}, 70%, 70%, ${0.4 - t * 0.03})`;
        ctx.lineWidth = 2 - t * 0.1;
        ctx.stroke();
      }

      // Bell (dome)
      ctx.beginPath();
      ctx.ellipse(0, 0, bellWidth, bellHeight, 0, Math.PI, Math.PI * 2);

      const bellGradient = ctx.createRadialGradient(0, -bellHeight * 0.3, 0, 0, 0, bellWidth);
      bellGradient.addColorStop(0, `hsla(${jelly.hue}, 70%, 80%, 0.6)`);
      bellGradient.addColorStop(0.5, `hsla(${jelly.hue}, 80%, 60%, 0.4)`);
      bellGradient.addColorStop(1, `hsla(${jelly.hue}, 90%, 40%, 0.2)`);
      ctx.fillStyle = bellGradient;
      ctx.fill();

      // Bell edge highlight
      ctx.beginPath();
      ctx.ellipse(0, 0, bellWidth, bellHeight, 0, Math.PI, Math.PI * 2);
      ctx.strokeStyle = `hsla(${jelly.hue}, 60%, 80%, 0.5)`;
      ctx.lineWidth = 2;
      ctx.stroke();

      // Inner bell detail
      ctx.beginPath();
      ctx.ellipse(0, bellHeight * 0.2, bellWidth * 0.7, bellHeight * 0.5, 0, Math.PI, Math.PI * 2);
      ctx.fillStyle = `hsla(${jelly.hue}, 80%, 70%, 0.2)`;
      ctx.fill();

      // Oral arms (frilly bits under bell)
      for (let a = 0; a < 4; a++) {
        const armX = ((a - 1.5) / 3) * bellWidth * 0.8;
        const armPhase = time * 0.002 + a;

        ctx.beginPath();
        ctx.moveTo(armX, bellHeight * 0.2);

        for (let i = 0; i <= 5; i++) {
          const progress = i / 5;
          const wave = Math.sin(armPhase + progress * 3) * 5;
          ctx.lineTo(armX + wave, bellHeight * 0.2 + jelly.size * 0.4 * progress);
        }

        ctx.strokeStyle = `hsla(${jelly.hue + 20}, 60%, 75%, 0.5)`;
        ctx.lineWidth = 3;
        ctx.stroke();
      }

      ctx.restore();
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawBackground();

      // Draw particles
      particlesRef.current.forEach((particle) => drawParticle(particle, time));

      // Draw jellyfish (sort by y for depth)
      jellyfishRef.current
        .sort((a, b) => a.y - b.y)
        .forEach((jelly) => drawJellyfish(jelly, time));

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
