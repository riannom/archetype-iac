/**
 * Eclipse Animation
 *
 * Slow-moving solar eclipse with corona effects,
 * stars appearing as darkness grows.
 */

import { useEffect, useRef } from 'react';

interface Star {
  x: number;
  y: number;
  size: number;
  twinklePhase: number;
  twinkleSpeed: number;
}

interface CoronaRay {
  angle: number;
  length: number;
  width: number;
  brightness: number;
  wobblePhase: number;
  wobbleSpeed: number;
}

interface Particle {
  x: number;
  y: number;
  size: number;
  angle: number;
  distance: number;
  speed: number;
  brightness: number;
}

export function useEclipse(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  enabled: boolean
) {
  const starsRef = useRef<Star[]>([]);
  const coronaRaysRef = useRef<CoronaRay[]>([]);
  const particlesRef = useRef<Particle[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef(0);
  const eclipsePhaseRef = useRef(0); // 0 = full sun, 1 = full eclipse, cycles slowly

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
    const sunX = width * 0.7;
    const sunY = height * 0.4;
    const sunRadius = Math.min(width, height) * 0.12;

    // Initialize stars
    starsRef.current = [];
    for (let i = 0; i < 150; i++) {
      starsRef.current.push({
        x: Math.random() * width,
        y: Math.random() * height,
        size: 0.5 + Math.random() * 1.5,
        twinklePhase: Math.random() * Math.PI * 2,
        twinkleSpeed: 0.02 + Math.random() * 0.03,
      });
    }

    // Initialize corona rays
    coronaRaysRef.current = [];
    for (let i = 0; i < 24; i++) {
      coronaRaysRef.current.push({
        angle: (i / 24) * Math.PI * 2,
        length: sunRadius * (1.5 + Math.random() * 1.5),
        width: 0.1 + Math.random() * 0.15,
        brightness: 0.3 + Math.random() * 0.4,
        wobblePhase: Math.random() * Math.PI * 2,
        wobbleSpeed: 0.02 + Math.random() * 0.02,
      });
    }

    particlesRef.current = [];

    const animate = () => {
      const currentWidth = canvas.width;
      const currentHeight = canvas.height;
      ctx.clearRect(0, 0, currentWidth, currentHeight);
      timeRef.current += 0.016;

      // Very slow eclipse cycle (about 60 seconds for full cycle)
      eclipsePhaseRef.current += 0.0003;
      // Oscillate between 0.1 and 1.0 (never fully uneclipsed for dramatic effect)
      const eclipseAmount = 0.1 + Math.sin(eclipsePhaseRef.current) * 0.45 + 0.45;

      // Sky color based on eclipse phase
      const skyDarkness = eclipseAmount;
      const skyGradient = ctx.createRadialGradient(
        sunX,
        sunY,
        sunRadius * 2,
        sunX,
        sunY,
        Math.max(currentWidth, currentHeight)
      );

      if (darkMode) {
        const coronaGlow = 0.15 * eclipseAmount;
        skyGradient.addColorStop(0, `rgba(60, 40, 80, ${coronaGlow})`);
        skyGradient.addColorStop(0.3, `rgba(20, 15, 40, ${0.5 + skyDarkness * 0.3})`);
        skyGradient.addColorStop(1, `rgba(5, 5, 15, ${0.8 + skyDarkness * 0.2})`);
      } else {
        const coronaGlow = 0.2 * eclipseAmount;
        skyGradient.addColorStop(0, `rgba(100, 70, 120, ${coronaGlow})`);
        skyGradient.addColorStop(0.3, `rgba(30, 25, 60, ${0.4 + skyDarkness * 0.4})`);
        skyGradient.addColorStop(1, `rgba(10, 10, 30, ${0.7 + skyDarkness * 0.3})`);
      }

      // Fill sky
      ctx.fillStyle = darkMode ? '#050510' : '#0a0a20';
      ctx.fillRect(0, 0, currentWidth, currentHeight);
      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, currentWidth, currentHeight);

      // Draw stars (visibility based on eclipse)
      const starVisibility = eclipseAmount;
      starsRef.current.forEach((star) => {
        star.twinklePhase += star.twinkleSpeed;
        const twinkle = 0.3 + Math.sin(star.twinklePhase) * 0.4;
        const alpha = twinkle * starVisibility;

        // Don't draw stars too close to the sun
        const distToSun = Math.hypot(star.x - sunX, star.y - sunY);
        if (distToSun < sunRadius * 3) return;

        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${alpha})`;
        ctx.fill();
      });

      // Corona effect (visible during eclipse)
      const coronaVisibility = eclipseAmount;

      // Outer corona glow
      const outerCorona = ctx.createRadialGradient(
        sunX,
        sunY,
        sunRadius * 0.9,
        sunX,
        sunY,
        sunRadius * 3.5
      );
      outerCorona.addColorStop(0, `rgba(255, 200, 150, ${coronaVisibility * 0.3})`);
      outerCorona.addColorStop(0.3, `rgba(255, 150, 100, ${coronaVisibility * 0.15})`);
      outerCorona.addColorStop(0.6, `rgba(200, 100, 80, ${coronaVisibility * 0.08})`);
      outerCorona.addColorStop(1, 'rgba(100, 50, 50, 0)');

      ctx.beginPath();
      ctx.arc(sunX, sunY, sunRadius * 3.5, 0, Math.PI * 2);
      ctx.fillStyle = outerCorona;
      ctx.fill();

      // Draw corona rays
      coronaRaysRef.current.forEach((ray) => {
        ray.wobblePhase += ray.wobbleSpeed;
        const wobble = Math.sin(ray.wobblePhase) * 0.1;

        ctx.save();
        ctx.translate(sunX, sunY);
        ctx.rotate(ray.angle + wobble);

        const rayGradient = ctx.createLinearGradient(
          sunRadius * 0.95,
          0,
          sunRadius + ray.length,
          0
        );
        rayGradient.addColorStop(0, `rgba(255, 220, 180, ${ray.brightness * coronaVisibility})`);
        rayGradient.addColorStop(0.3, `rgba(255, 180, 120, ${ray.brightness * 0.5 * coronaVisibility})`);
        rayGradient.addColorStop(1, 'rgba(255, 150, 100, 0)');

        ctx.beginPath();
        ctx.moveTo(sunRadius * 0.95, -ray.width * sunRadius);
        ctx.lineTo(sunRadius + ray.length, 0);
        ctx.lineTo(sunRadius * 0.95, ray.width * sunRadius);
        ctx.closePath();

        ctx.fillStyle = rayGradient;
        ctx.fill();

        ctx.restore();
      });

      // Inner corona (bright ring around eclipsed sun)
      const innerCorona = ctx.createRadialGradient(
        sunX,
        sunY,
        sunRadius * 0.85,
        sunX,
        sunY,
        sunRadius * 1.3
      );
      innerCorona.addColorStop(0, `rgba(255, 255, 255, ${coronaVisibility * 0.8})`);
      innerCorona.addColorStop(0.4, `rgba(255, 220, 180, ${coronaVisibility * 0.4})`);
      innerCorona.addColorStop(1, 'rgba(255, 200, 150, 0)');

      ctx.beginPath();
      ctx.arc(sunX, sunY, sunRadius * 1.3, 0, Math.PI * 2);
      ctx.fillStyle = innerCorona;
      ctx.fill();

      // The sun (partially visible based on eclipse phase)
      const sunVisibility = 1 - eclipseAmount;
      if (sunVisibility > 0) {
        const sunGlow = ctx.createRadialGradient(
          sunX,
          sunY,
          0,
          sunX,
          sunY,
          sunRadius
        );
        sunGlow.addColorStop(0, `rgba(255, 255, 200, ${sunVisibility})`);
        sunGlow.addColorStop(0.7, `rgba(255, 220, 100, ${sunVisibility * 0.8})`);
        sunGlow.addColorStop(1, `rgba(255, 180, 50, ${sunVisibility * 0.5})`);

        ctx.beginPath();
        ctx.arc(sunX, sunY, sunRadius, 0, Math.PI * 2);
        ctx.fillStyle = sunGlow;
        ctx.fill();
      }

      // The moon (covering the sun)
      const moonOffset = (1 - eclipseAmount) * sunRadius * 2;
      ctx.beginPath();
      ctx.arc(sunX + moonOffset, sunY, sunRadius * 1.02, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? '#0a0a10' : '#0f0f18';
      ctx.fill();

      // Diamond ring effect (brief moment during transition)
      const transitionIntensity = Math.abs(Math.sin(eclipsePhaseRef.current * 2)) < 0.1 ? 1 : 0;
      if (transitionIntensity > 0) {
        const diamondAngle = Math.PI * 0.3;
        const diamondX = sunX + Math.cos(diamondAngle) * sunRadius * 0.95;
        const diamondY = sunY + Math.sin(diamondAngle) * sunRadius * 0.95;

        const diamondGlow = ctx.createRadialGradient(
          diamondX,
          diamondY,
          0,
          diamondX,
          diamondY,
          sunRadius * 0.5
        );
        diamondGlow.addColorStop(0, `rgba(255, 255, 255, ${transitionIntensity * 0.9})`);
        diamondGlow.addColorStop(0.2, `rgba(255, 240, 200, ${transitionIntensity * 0.5})`);
        diamondGlow.addColorStop(1, 'rgba(255, 220, 150, 0)');

        ctx.beginPath();
        ctx.arc(diamondX, diamondY, sunRadius * 0.5, 0, Math.PI * 2);
        ctx.fillStyle = diamondGlow;
        ctx.fill();
      }

      // Spawn drifting particles around corona
      if (Math.random() < 0.05 * coronaVisibility) {
        const angle = Math.random() * Math.PI * 2;
        particlesRef.current.push({
          x: sunX,
          y: sunY,
          size: 1 + Math.random() * 1.5,
          angle,
          distance: sunRadius * (1 + Math.random() * 0.5),
          speed: 0.2 + Math.random() * 0.3,
          brightness: 0.3 + Math.random() * 0.4,
        });
      }

      // Update and draw particles
      particlesRef.current = particlesRef.current.filter((p) => {
        p.distance += p.speed;
        p.brightness -= 0.003;

        if (p.brightness <= 0 || p.distance > sunRadius * 4) return false;

        const px = sunX + Math.cos(p.angle) * p.distance;
        const py = sunY + Math.sin(p.angle) * p.distance;

        ctx.beginPath();
        ctx.arc(px, py, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 200, 150, ${p.brightness * coronaVisibility})`;
        ctx.fill();

        return true;
      });

      // Limit particles
      if (particlesRef.current.length > 50) {
        particlesRef.current = particlesRef.current.slice(-40);
      }

      // Subtle horizon glow
      const horizonY = currentHeight * 0.85;
      const horizonGlow = ctx.createLinearGradient(0, horizonY, 0, currentHeight);
      horizonGlow.addColorStop(0, 'rgba(60, 40, 80, 0)');
      horizonGlow.addColorStop(0.5, `rgba(80, 50, 100, ${0.1 * (1 - eclipseAmount)})`);
      horizonGlow.addColorStop(1, `rgba(100, 60, 80, ${0.15 * (1 - eclipseAmount)})`);

      ctx.fillStyle = horizonGlow;
      ctx.fillRect(0, horizonY, currentWidth, currentHeight - horizonY);

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
