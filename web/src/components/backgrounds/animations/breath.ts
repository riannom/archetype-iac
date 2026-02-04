/**
 * Breath Animation
 *
 * The screen slowly pulses - organic shapes gently expand and contract
 * like the universe breathing. Minimal, hypnotic, deeply calming.
 */

import { useEffect, useRef } from 'react';

interface BreathOrb {
  x: number;
  y: number;
  baseRadius: number;
  phase: number;
  phaseSpeed: number;
  hue: number;
  layers: number;
}

export function useBreath(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const orbsRef = useRef<BreathOrb[]>([]);
  const animationRef = useRef<number>(0);
  const globalPhaseRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeOrbs();
    };

    const initializeOrbs = () => {
      const { width, height } = canvas;
      orbsRef.current = [];

      // Create 3-5 breathing orbs
      const orbCount = 3 + Math.floor(Math.random() * 3);
      const hues = darkMode
        ? [220, 260, 280, 200, 180] // Cool, muted tones for dark
        : [200, 280, 320, 40, 160]; // Varied for light

      for (let i = 0; i < orbCount; i++) {
        orbsRef.current.push({
          x: width * 0.2 + Math.random() * width * 0.6,
          y: height * 0.2 + Math.random() * height * 0.6,
          baseRadius: 80 + Math.random() * 120,
          phase: (i / orbCount) * Math.PI * 2, // Stagger phases
          phaseSpeed: 0.008 + Math.random() * 0.004, // Very slow breathing
          hue: hues[i % hues.length],
          layers: 4 + Math.floor(Math.random() * 3),
        });
      }
    };

    const easeBreath = (t: number): number => {
      // Custom easing for natural breathing rhythm
      // Slower at full inhale/exhale, faster in between
      return (Math.sin(t) + 1) / 2 * (0.7 + 0.3 * Math.sin(t * 2));
    };

    const drawOrb = (orb: BreathOrb) => {
      const opacityMult = (opacity / 50) * 0.4;
      const breathAmount = easeBreath(orb.phase);
      const globalBreath = easeBreath(globalPhaseRef.current);

      // Combine individual and global breathing
      const combinedBreath = breathAmount * 0.7 + globalBreath * 0.3;
      const currentRadius = orb.baseRadius * (0.6 + combinedBreath * 0.8);

      const lightness = darkMode ? 45 : 65;
      const saturation = darkMode ? 30 : 40;

      // Draw multiple soft layers
      for (let layer = orb.layers - 1; layer >= 0; layer--) {
        const layerRatio = layer / orb.layers;
        const layerRadius = currentRadius * (0.3 + layerRatio * 0.7);
        const layerOpacity = opacityMult * (1 - layerRatio * 0.6);

        // Subtle hue shift per layer
        const layerHue = orb.hue + layer * 5;

        const gradient = ctx.createRadialGradient(
          orb.x, orb.y, 0,
          orb.x, orb.y, layerRadius
        );

        gradient.addColorStop(0, `hsla(${layerHue}, ${saturation + 10}%, ${lightness + 10}%, ${layerOpacity * 0.6})`);
        gradient.addColorStop(0.5, `hsla(${layerHue}, ${saturation}%, ${lightness}%, ${layerOpacity * 0.3})`);
        gradient.addColorStop(1, `hsla(${layerHue}, ${saturation - 10}%, ${lightness - 5}%, 0)`);

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(orb.x, orb.y, layerRadius, 0, Math.PI * 2);
        ctx.fill();
      }

      // Inner glow that pulses
      const glowIntensity = 0.1 + combinedBreath * 0.15;
      const innerGlow = ctx.createRadialGradient(
        orb.x, orb.y, 0,
        orb.x, orb.y, currentRadius * 0.3
      );
      innerGlow.addColorStop(0, `hsla(${orb.hue + 30}, ${saturation + 20}%, ${lightness + 20}%, ${glowIntensity * opacityMult})`);
      innerGlow.addColorStop(1, 'hsla(0, 0%, 100%, 0)');

      ctx.fillStyle = innerGlow;
      ctx.beginPath();
      ctx.arc(orb.x, orb.y, currentRadius * 0.3, 0, Math.PI * 2);
      ctx.fill();
    };

    const animate = () => {
      const { width, height } = canvas;

      // Clear canvas
      ctx.fillStyle = darkMode ? 'rgba(15, 15, 20, 1)' : 'rgba(252, 250, 248, 1)';
      ctx.fillRect(0, 0, width, height);

      // Update global phase (collective breathing)
      globalPhaseRef.current += 0.006;

      // Update and draw orbs
      orbsRef.current.forEach((orb) => {
        orb.phase += orb.phaseSpeed;

        // Very slow drift
        orb.x += Math.sin(orb.phase * 0.3) * 0.1;
        orb.y += Math.cos(orb.phase * 0.2) * 0.1;

        // Keep in bounds with soft wrapping
        if (orb.x < -orb.baseRadius) orb.x = width + orb.baseRadius;
        if (orb.x > width + orb.baseRadius) orb.x = -orb.baseRadius;
        if (orb.y < -orb.baseRadius) orb.y = height + orb.baseRadius;
        if (orb.y > height + orb.baseRadius) orb.y = -orb.baseRadius;

        drawOrb(orb);
      });

      // Subtle overlay pulse
      const overlayAlpha = 0.02 * (0.5 + 0.5 * Math.sin(globalPhaseRef.current));
      ctx.fillStyle = darkMode
        ? `rgba(40, 50, 80, ${overlayAlpha})`
        : `rgba(255, 240, 230, ${overlayAlpha})`;
      ctx.fillRect(0, 0, width, height);

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
