/**
 * Wind Chimes Animation
 *
 * Delicate hanging wind chimes with gentle swaying motion.
 * Soft glowing rings visualize the subtle sounds.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface Chime {
  x: number;
  baseY: number;
  length: number;
  width: number;
  swingPhase: number;
  swingSpeed: number;
  swingAmplitude: number;
  material: 'brass' | 'silver' | 'copper' | 'glass';
  resonance: number;
  targetResonance: number;
}

interface SoundRing {
  x: number;
  y: number;
  radius: number;
  maxRadius: number;
  opacity: number;
  hue: number;
}

interface Star {
  x: number;
  y: number;
  size: number;
  twinklePhase: number;
}

export function useWindChimes(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const chimesRef = useRef<Chime[]>([]);
  const soundRingsRef = useRef<SoundRing[]>([]);
  const starsRef = useRef<Star[]>([]);
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

      // Create chimes
      chimesRef.current = [];
      const chimeCount = 5 + Math.floor(width / 250);
      const materials: ('brass' | 'silver' | 'copper' | 'glass')[] = ['brass', 'silver', 'copper', 'glass'];

      for (let i = 0; i < chimeCount; i++) {
        const spacing = width / (chimeCount + 1);
        chimesRef.current.push({
          x: spacing * (i + 1) + (Math.random() - 0.5) * spacing * 0.3,
          baseY: 30 + Math.random() * 50,
          length: 80 + Math.random() * 120,
          width: 8 + Math.random() * 8,
          swingPhase: Math.random() * Math.PI * 2,
          swingSpeed: 0.008 + Math.random() * 0.006,
          swingAmplitude: 0.05 + Math.random() * 0.1,
          material: materials[Math.floor(Math.random() * materials.length)],
          resonance: 0,
          targetResonance: 0,
        });
      }

      // Create stars for night sky
      starsRef.current = [];
      for (let i = 0; i < 60; i++) {
        starsRef.current.push({
          x: Math.random() * width,
          y: Math.random() * height * 0.6,
          size: 0.5 + Math.random() * 1.5,
          twinklePhase: Math.random() * Math.PI * 2,
        });
      }

      soundRingsRef.current = [];
    };

    const getMaterialColors = (material: Chime['material'], _darkMode: boolean) => {
      const colors: Record<Chime['material'], { body: string; highlight: string; shadow: string }> = {
        brass: { body: '#c9a227', highlight: '#e8c547', shadow: '#8a6d1b' },
        silver: { body: '#a8a8a8', highlight: '#d0d0d0', shadow: '#686868' },
        copper: { body: '#b87333', highlight: '#da9054', shadow: '#8a5522' },
        glass: { body: 'rgba(180, 220, 255, 0.6)', highlight: 'rgba(255, 255, 255, 0.8)', shadow: 'rgba(100, 150, 200, 0.4)' },
      };
      return colors[material];
    };

    const drawBackground = () => {
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
      if (darkMode) {
        gradient.addColorStop(0, '#0a0a15');
        gradient.addColorStop(0.4, '#101525');
        gradient.addColorStop(1, '#0a1020');
      } else {
        gradient.addColorStop(0, '#4a5568');
        gradient.addColorStop(0.4, '#667788');
        gradient.addColorStop(1, '#556677');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // Moon glow
      const moonX = canvas.width * 0.15;
      const moonY = canvas.height * 0.15;
      const moonGlow = ctx.createRadialGradient(moonX, moonY, 0, moonX, moonY, 150);
      moonGlow.addColorStop(0, darkMode ? 'rgba(255, 255, 220, 0.3)' : 'rgba(255, 255, 200, 0.2)');
      moonGlow.addColorStop(0.5, darkMode ? 'rgba(255, 255, 220, 0.1)' : 'rgba(255, 255, 200, 0.05)');
      moonGlow.addColorStop(1, 'transparent');
      ctx.fillStyle = moonGlow;
      ctx.beginPath();
      ctx.arc(moonX, moonY, 150, 0, Math.PI * 2);
      ctx.fill();

      // Moon
      ctx.beginPath();
      ctx.arc(moonX, moonY, 40, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? '#ffffd8' : '#fff8e0';
      ctx.fill();
    };

    const drawStars = (time: number) => {
      starsRef.current.forEach((star) => {
        const twinkle = Math.sin(time * 0.002 + star.twinklePhase) * 0.4 + 0.6;
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size * twinkle, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${0.5 * twinkle})`;
        ctx.fill();
      });
    };

    const drawHangingRod = () => {
      // Top rod that chimes hang from
      ctx.beginPath();
      ctx.moveTo(canvas.width * 0.1, 25);
      ctx.lineTo(canvas.width * 0.9, 25);
      ctx.strokeStyle = darkMode ? '#4a4a4a' : '#3a3a3a';
      ctx.lineWidth = 4;
      ctx.lineCap = 'round';
      ctx.stroke();
    };

    const drawChime = (chime: Chime, time: number) => {
      // Update swing
      chime.swingPhase += chime.swingSpeed;
      const swing = Math.sin(chime.swingPhase) * chime.swingAmplitude;

      // Smooth resonance transitions
      chime.resonance += (chime.targetResonance - chime.resonance) * 0.05;
      chime.targetResonance *= 0.995;

      // Occasionally trigger resonance (simulating wind)
      if (Math.random() > 0.998) {
        chime.targetResonance = 0.5 + Math.random() * 0.5;

        // Create sound ring
        soundRingsRef.current.push({
          x: chime.x,
          y: chime.baseY + chime.length * 0.5,
          radius: chime.width,
          maxRadius: 50 + chime.length * 0.5,
          opacity: 0.6,
          hue: chime.material === 'glass' ? 200 : chime.material === 'copper' ? 30 : chime.material === 'brass' ? 45 : 0,
        });
      }

      ctx.save();
      ctx.translate(chime.x, chime.baseY);
      ctx.rotate(swing);

      const colors = getMaterialColors(chime.material, darkMode);

      // String
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(0, 15);
      ctx.strokeStyle = '#888';
      ctx.lineWidth = 1;
      ctx.stroke();

      // Chime tube
      const tubeGradient = ctx.createLinearGradient(-chime.width / 2, 0, chime.width / 2, 0);
      if (chime.material === 'glass') {
        tubeGradient.addColorStop(0, 'rgba(150, 200, 255, 0.3)');
        tubeGradient.addColorStop(0.3, 'rgba(200, 230, 255, 0.5)');
        tubeGradient.addColorStop(0.5, 'rgba(255, 255, 255, 0.6)');
        tubeGradient.addColorStop(0.7, 'rgba(200, 230, 255, 0.5)');
        tubeGradient.addColorStop(1, 'rgba(150, 200, 255, 0.3)');
      } else {
        tubeGradient.addColorStop(0, colors.shadow);
        tubeGradient.addColorStop(0.3, colors.body);
        tubeGradient.addColorStop(0.5, colors.highlight);
        tubeGradient.addColorStop(0.7, colors.body);
        tubeGradient.addColorStop(1, colors.shadow);
      }

      ctx.beginPath();
      ctx.roundRect(-chime.width / 2, 15, chime.width, chime.length, 3);
      ctx.fillStyle = tubeGradient;
      ctx.fill();

      // Resonance glow
      if (chime.resonance > 0.05) {
        ctx.shadowColor = chime.material === 'glass' ? '#88ccff' : colors.highlight;
        ctx.shadowBlur = 15 * chime.resonance;
        ctx.fill();
        ctx.shadowBlur = 0;
      }

      // Top cap
      ctx.beginPath();
      ctx.ellipse(0, 15, chime.width / 2 + 2, 3, 0, 0, Math.PI * 2);
      ctx.fillStyle = colors.body;
      ctx.fill();

      // Bottom cap
      ctx.beginPath();
      ctx.ellipse(0, 15 + chime.length, chime.width / 2 + 2, 3, 0, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
    };

    const drawSoundRings = () => {
      soundRingsRef.current.forEach((ring, index) => {
        ring.radius += 1;
        ring.opacity -= 0.01;

        if (ring.opacity <= 0 || ring.radius > ring.maxRadius) {
          soundRingsRef.current.splice(index, 1);
          return;
        }

        ctx.beginPath();
        ctx.arc(ring.x, ring.y, ring.radius, 0, Math.PI * 2);
        ctx.strokeStyle = ring.hue === 0
          ? `rgba(200, 200, 200, ${ring.opacity * 0.5})`
          : `hsla(${ring.hue}, 60%, 70%, ${ring.opacity * 0.5})`;
        ctx.lineWidth = 2;
        ctx.stroke();
      });
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawBackground();
      drawStars(time);
      drawHangingRod();

      // Draw chimes
      chimesRef.current.forEach((chime) => drawChime(chime, time));

      // Draw sound visualization
      drawSoundRings();

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
