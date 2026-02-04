/**
 * Sunset Sailing Animation
 *
 * Sailboats gliding peacefully on calm water at sunset.
 * Gentle waves and reflections create a serene atmosphere.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface Sailboat {
  x: number;
  y: number;
  size: number;
  speed: number;
  bobPhase: number;
  sailColor: string;
  hullColor: string;
}

interface Wave {
  x: number;
  y: number;
  width: number;
  phase: number;
  speed: number;
  amplitude: number;
}

interface Bird {
  x: number;
  y: number;
  wingPhase: number;
  speed: number;
  size: number;
}

interface Cloud {
  x: number;
  y: number;
  width: number;
  speed: number;
  puffs: { offsetX: number; offsetY: number; size: number }[];
}

export function useSunsetSailing(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const boatsRef = useRef<Sailboat[]>([]);
  const wavesRef = useRef<Wave[]>([]);
  const birdsRef = useRef<Bird[]>([]);
  const cloudsRef = useRef<Cloud[]>([]);
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

      // Create sailboats
      boatsRef.current = [];
      const boatCount = 2 + Math.floor(Math.random() * 2);
      const sailColors = ['#FFFFFF', '#FFF8DC', '#FFFAF0'];
      const hullColors = ['#8B4513', '#A0522D', '#654321', '#2F4F4F'];

      for (let i = 0; i < boatCount; i++) {
        boatsRef.current.push({
          x: width * 0.1 + Math.random() * width * 0.8,
          y: height * 0.55 + Math.random() * height * 0.1,
          size: 30 + Math.random() * 30,
          speed: 0.1 + Math.random() * 0.15,
          bobPhase: Math.random() * Math.PI * 2,
          sailColor: sailColors[Math.floor(Math.random() * sailColors.length)],
          hullColor: hullColors[Math.floor(Math.random() * hullColors.length)],
        });
      }

      // Create waves
      wavesRef.current = [];
      for (let y = height * 0.5; y < height; y += 15) {
        for (let x = 0; x < width + 100; x += 80) {
          wavesRef.current.push({
            x: x + (Math.random() - 0.5) * 30,
            y: y + (Math.random() - 0.5) * 5,
            width: 40 + Math.random() * 40,
            phase: Math.random() * Math.PI * 2,
            speed: 0.002 + Math.random() * 0.002,
            amplitude: 2 + Math.random() * 3,
          });
        }
      }

      // Create birds
      birdsRef.current = [];
      for (let i = 0; i < 5; i++) {
        birdsRef.current.push({
          x: Math.random() * width,
          y: height * 0.15 + Math.random() * height * 0.2,
          wingPhase: Math.random() * Math.PI * 2,
          speed: 0.3 + Math.random() * 0.3,
          size: 8 + Math.random() * 6,
        });
      }

      // Create clouds
      cloudsRef.current = [];
      for (let i = 0; i < 4; i++) {
        const puffs: { offsetX: number; offsetY: number; size: number }[] = [];
        const puffCount = 4 + Math.floor(Math.random() * 3);
        for (let p = 0; p < puffCount; p++) {
          puffs.push({
            offsetX: (p - puffCount / 2) * 25 + (Math.random() - 0.5) * 15,
            offsetY: (Math.random() - 0.5) * 15,
            size: 20 + Math.random() * 20,
          });
        }
        cloudsRef.current.push({
          x: Math.random() * width * 1.5,
          y: 30 + Math.random() * height * 0.15,
          width: 70 + Math.random() * 50,
          speed: 0.05 + Math.random() * 0.1,
          puffs,
        });
      }

    };

    const drawSky = () => {
      const height = canvas.height;
      const gradient = ctx.createLinearGradient(0, 0, 0, height * 0.55);

      if (darkMode) {
        gradient.addColorStop(0, '#1a1a2e');
        gradient.addColorStop(0.3, '#2d2d44');
        gradient.addColorStop(0.6, '#4a3a5a');
        gradient.addColorStop(1, '#5a4a6a');
      } else {
        gradient.addColorStop(0, '#1e3c72');
        gradient.addColorStop(0.3, '#ff6b6b');
        gradient.addColorStop(0.5, '#ff8e53');
        gradient.addColorStop(0.7, '#feca57');
        gradient.addColorStop(1, '#ffeaa7');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, height * 0.55);
    };

    const drawSun = () => {
      // Only draw sun in dark mode
      if (!darkMode) return;

      const sunX = canvas.width * 0.7;
      const sunY = canvas.height * 0.35;
      const sunRadius = 40;

      // Sun disc only (no glow)
      ctx.beginPath();
      ctx.arc(sunX, sunY, sunRadius, 0, Math.PI * 2);
      const sunGradient = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, sunRadius);
      sunGradient.addColorStop(0, '#d4c4b4');
      sunGradient.addColorStop(1, '#a49484');
      ctx.fillStyle = sunGradient;
      ctx.fill();
    };

    const drawClouds = () => {
      cloudsRef.current.forEach((cloud) => {
        cloud.x += cloud.speed;
        if (cloud.x > canvas.width + 100) {
          cloud.x = -150;
        }

        cloud.puffs.forEach((puff) => {
          ctx.beginPath();
          ctx.arc(cloud.x + puff.offsetX, cloud.y + puff.offsetY, puff.size, 0, Math.PI * 2);
          ctx.fillStyle = darkMode
            ? 'rgba(100, 90, 110, 0.4)'
            : 'rgba(255, 200, 150, 0.5)';
          ctx.fill();
        });
      });
    };

    const drawWater = (time: number) => {
      const height = canvas.height;
      const width = canvas.width;

      // Water base
      const waterGradient = ctx.createLinearGradient(0, height * 0.5, 0, height);
      if (darkMode) {
        waterGradient.addColorStop(0, '#3a4a5a');
        waterGradient.addColorStop(0.5, '#2a3a4a');
        waterGradient.addColorStop(1, '#1a2a3a');
      } else {
        waterGradient.addColorStop(0, '#ff9a56');
        waterGradient.addColorStop(0.3, '#4169e1');
        waterGradient.addColorStop(0.7, '#1e90ff');
        waterGradient.addColorStop(1, '#000080');
      }
      ctx.fillStyle = waterGradient;
      ctx.fillRect(0, height * 0.5, width, height * 0.5);

      // Sun reflection - only in dark mode (sun is only drawn in dark mode)
      if (darkMode) {
        const reflectionX = width * 0.7;
        const reflectionGradient = ctx.createLinearGradient(reflectionX, height * 0.5, reflectionX, height);
        reflectionGradient.addColorStop(0, 'rgba(200, 180, 160, 0.3)');
        reflectionGradient.addColorStop(1, 'rgba(200, 180, 160, 0.05)');
        ctx.fillStyle = reflectionGradient;
        ctx.beginPath();
        ctx.moveTo(reflectionX - 30, height * 0.5);
        ctx.lineTo(reflectionX + 30, height * 0.5);
        ctx.lineTo(reflectionX + 80, height);
        ctx.lineTo(reflectionX - 80, height);
        ctx.closePath();
        ctx.fill();
      }

      // Wave highlights
      wavesRef.current.forEach((wave) => {
        const waveY = wave.y + Math.sin(time * wave.speed + wave.phase) * wave.amplitude;
        ctx.beginPath();
        ctx.moveTo(wave.x - wave.width / 2, waveY);
        ctx.quadraticCurveTo(wave.x, waveY - 3, wave.x + wave.width / 2, waveY);
        ctx.strokeStyle = darkMode
          ? 'rgba(120, 140, 160, 0.2)'
          : 'rgba(255, 255, 255, 0.15)';
        ctx.lineWidth = 1;
        ctx.stroke();
      });
    };

    const drawSailboat = (boat: Sailboat, time: number) => {
      const bob = Math.sin(time * 0.002 + boat.bobPhase) * 3;
      const tilt = Math.sin(time * 0.0015 + boat.bobPhase) * 0.05;

      ctx.save();
      ctx.translate(boat.x, boat.y + bob);
      ctx.rotate(tilt);

      const size = boat.size;

      // Hull
      ctx.beginPath();
      ctx.moveTo(-size, 0);
      ctx.quadraticCurveTo(-size * 0.8, size * 0.4, 0, size * 0.3);
      ctx.quadraticCurveTo(size * 0.8, size * 0.4, size, 0);
      ctx.lineTo(size * 0.9, -size * 0.1);
      ctx.lineTo(-size * 0.9, -size * 0.1);
      ctx.closePath();
      ctx.fillStyle = boat.hullColor;
      ctx.fill();

      // Mast
      ctx.beginPath();
      ctx.moveTo(0, -size * 0.1);
      ctx.lineTo(0, -size * 1.5);
      ctx.strokeStyle = '#4a3a2a';
      ctx.lineWidth = 3;
      ctx.stroke();

      // Main sail
      ctx.beginPath();
      ctx.moveTo(0, -size * 1.4);
      ctx.quadraticCurveTo(size * 0.8, -size * 0.8, size * 0.6, -size * 0.2);
      ctx.lineTo(0, -size * 0.2);
      ctx.closePath();
      ctx.fillStyle = boat.sailColor;
      ctx.fill();

      // Small sail
      ctx.beginPath();
      ctx.moveTo(0, -size * 1.4);
      ctx.quadraticCurveTo(-size * 0.4, -size * 0.9, -size * 0.3, -size * 0.4);
      ctx.lineTo(0, -size * 0.4);
      ctx.closePath();
      ctx.fillStyle = boat.sailColor;
      ctx.globalAlpha = 0.9;
      ctx.fill();
      ctx.globalAlpha = 1;

      ctx.restore();

      // Move boat
      boat.x += boat.speed;
      if (boat.x > canvas.width + boat.size * 2) {
        boat.x = -boat.size * 2;
      }
    };

    const drawBirds = (time: number) => {
      birdsRef.current.forEach((bird) => {
        bird.x += bird.speed;
        bird.wingPhase += 0.1;

        if (bird.x > canvas.width + 50) {
          bird.x = -50;
          bird.y = canvas.height * 0.15 + Math.random() * canvas.height * 0.2;
        }

        const wingAngle = Math.sin(bird.wingPhase) * 0.6;

        ctx.save();
        ctx.translate(bird.x, bird.y);
        ctx.fillStyle = darkMode ? '#2a2a3a' : '#1a1a2a';

        // Left wing
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.quadraticCurveTo(
          -bird.size * 0.5,
          -bird.size * wingAngle,
          -bird.size,
          -bird.size * 0.3 * wingAngle
        );
        ctx.lineWidth = 2;
        ctx.strokeStyle = darkMode ? '#2a2a3a' : '#1a1a2a';
        ctx.stroke();

        // Right wing
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.quadraticCurveTo(
          bird.size * 0.5,
          -bird.size * wingAngle,
          bird.size,
          -bird.size * 0.3 * wingAngle
        );
        ctx.stroke();

        ctx.restore();
      });
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawSky();
      drawSun();
      drawClouds();
      drawWater(time);

      // Draw boats sorted by y position
      const sortedBoats = [...boatsRef.current].sort((a, b) => a.y - b.y);
      sortedBoats.forEach((boat) => drawSailboat(boat, time));

      drawBirds(time);

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
