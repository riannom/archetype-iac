/**
 * Misty Valley Animation (replaces mountains)
 *
 * Layered mountain silhouettes with drifting mist and soft lighting.
 * Pre-generates all random values to avoid flickering.
 */

import { useEffect, useRef } from 'react';

interface MountainLayer {
  baseY: number;
  peaks: { x: number; height: number }[];
  color: string;
  parallaxSpeed: number;
}

interface MistCloud {
  x: number;
  y: number;
  width: number;
  height: number;
  opacity: number;
  targetOpacity: number;
  speed: number;
  puffs: { offsetX: number; offsetY: number; size: number }[];
}

interface Bird {
  x: number;
  y: number;
  speed: number;
  wingPhase: number;
  size: number;
}

export function useMistyValley(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const mountainsRef = useRef<MountainLayer[]>([]);
  const mistRef = useRef<MistCloud[]>([]);
  const birdsRef = useRef<Bird[]>([]);
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

      // Create mountain layers (back to front)
      mountainsRef.current = [];

      const layerConfigs = darkMode
        ? [
            { baseY: height * 0.35, color: '#1a1a2a', parallax: 0.1 },
            { baseY: height * 0.45, color: '#252535', parallax: 0.2 },
            { baseY: height * 0.55, color: '#303045', parallax: 0.3 },
            { baseY: height * 0.65, color: '#3a3a55', parallax: 0.4 },
          ]
        : [
            { baseY: height * 0.35, color: '#7B8FA0', parallax: 0.1 },
            { baseY: height * 0.45, color: '#8FA5B5', parallax: 0.2 },
            { baseY: height * 0.55, color: '#A3BACA', parallax: 0.3 },
            { baseY: height * 0.65, color: '#B7CFDF', parallax: 0.4 },
          ];

      layerConfigs.forEach((config) => {
        const peaks: { x: number; height: number }[] = [];
        const peakCount = 5 + Math.floor(Math.random() * 4);

        for (let p = 0; p < peakCount; p++) {
          peaks.push({
            x: (p / (peakCount - 1)) * width * 1.2 - width * 0.1,
            height: 50 + Math.random() * 150,
          });
        }

        mountainsRef.current.push({
          baseY: config.baseY,
          peaks,
          color: config.color,
          parallaxSpeed: config.parallax,
        });
      });

      // Create mist clouds
      mistRef.current = [];
      for (let i = 0; i < 8; i++) {
        const puffs: { offsetX: number; offsetY: number; size: number }[] = [];
        const puffCount = 5 + Math.floor(Math.random() * 5);
        for (let p = 0; p < puffCount; p++) {
          puffs.push({
            offsetX: (Math.random() - 0.5) * 150,
            offsetY: (Math.random() - 0.5) * 40,
            size: 40 + Math.random() * 60,
          });
        }

        mistRef.current.push({
          x: Math.random() * width * 1.5 - width * 0.25,
          y: height * 0.4 + Math.random() * height * 0.4,
          width: 150 + Math.random() * 200,
          height: 40 + Math.random() * 40,
          opacity: 0.2 + Math.random() * 0.3,
          targetOpacity: 0.2 + Math.random() * 0.3,
          speed: 0.1 + Math.random() * 0.2,
          puffs,
        });
      }

      // Create distant birds
      birdsRef.current = [];
      for (let i = 0; i < 4; i++) {
        birdsRef.current.push({
          x: Math.random() * width,
          y: height * 0.2 + Math.random() * height * 0.2,
          speed: 0.3 + Math.random() * 0.2,
          wingPhase: Math.random() * Math.PI * 2,
          size: 3 + Math.random() * 3,
        });
      }
    };

    const drawSky = () => {
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height * 0.6);
      if (darkMode) {
        gradient.addColorStop(0, '#0a0a15');
        gradient.addColorStop(0.5, '#151525');
        gradient.addColorStop(1, '#1a1a2a');
      } else {
        gradient.addColorStop(0, '#FFE4B5');
        gradient.addColorStop(0.3, '#FFDAB9');
        gradient.addColorStop(0.6, '#E6D5C3');
        gradient.addColorStop(1, '#D4C4B0');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // Sun/moon glow
      const glowX = canvas.width * 0.75;
      const glowY = canvas.height * 0.15;
      const glowGradient = ctx.createRadialGradient(glowX, glowY, 0, glowX, glowY, 200);
      if (darkMode) {
        glowGradient.addColorStop(0, 'rgba(200, 200, 220, 0.3)');
        glowGradient.addColorStop(0.5, 'rgba(150, 150, 180, 0.1)');
        glowGradient.addColorStop(1, 'transparent');
      } else {
        glowGradient.addColorStop(0, 'rgba(255, 230, 180, 0.6)');
        glowGradient.addColorStop(0.5, 'rgba(255, 220, 150, 0.2)');
        glowGradient.addColorStop(1, 'transparent');
      }
      ctx.fillStyle = glowGradient;
      ctx.fillRect(glowX - 200, glowY - 200, 400, 400);
    };

    const drawMountainLayer = (layer: MountainLayer, time: number) => {
      const parallaxOffset = Math.sin(time * 0.0001) * 20 * layer.parallaxSpeed;

      ctx.beginPath();
      ctx.moveTo(-50 + parallaxOffset, canvas.height);

      layer.peaks.forEach((peak, index) => {
        const prevPeak = layer.peaks[index - 1] || { x: -100, height: 50 };
        const nextPeak = layer.peaks[index + 1] || { x: canvas.width + 100, height: 50 };

        // Create smooth mountain shape
        const peakX = peak.x + parallaxOffset;
        const peakY = layer.baseY - peak.height;

        if (index === 0) {
          ctx.lineTo(peakX - 50, layer.baseY);
        }

        // Rising edge
        ctx.quadraticCurveTo(
          (prevPeak.x + peak.x) / 2 + parallaxOffset,
          layer.baseY - peak.height * 0.3,
          peakX,
          peakY
        );

        // Falling edge
        ctx.quadraticCurveTo(
          (peak.x + nextPeak.x) / 2 + parallaxOffset,
          layer.baseY - peak.height * 0.3,
          nextPeak.x + parallaxOffset,
          layer.baseY - nextPeak.height
        );
      });

      ctx.lineTo(canvas.width + 50 + parallaxOffset, canvas.height);
      ctx.closePath();

      ctx.fillStyle = layer.color;
      ctx.fill();
    };

    const drawMist = (mist: MistCloud) => {
      // Smooth opacity transition
      mist.opacity += (mist.targetOpacity - mist.opacity) * 0.01;

      // Occasionally change target opacity
      if (Math.random() > 0.995) {
        mist.targetOpacity = 0.15 + Math.random() * 0.35;
      }

      // Move mist
      mist.x += mist.speed;
      if (mist.x > canvas.width + mist.width) {
        mist.x = -mist.width;
      }

      // Draw mist puffs
      mist.puffs.forEach((puff) => {
        ctx.beginPath();
        ctx.arc(
          mist.x + puff.offsetX,
          mist.y + puff.offsetY,
          puff.size,
          0,
          Math.PI * 2
        );
        ctx.fillStyle = darkMode
          ? `rgba(100, 100, 120, ${mist.opacity * 0.5})`
          : `rgba(255, 255, 255, ${mist.opacity})`;
        ctx.fill();
      });
    };

    const drawBird = (bird: Bird, time: number) => {
      bird.x += bird.speed;
      bird.wingPhase += 0.1;

      if (bird.x > canvas.width + 50) {
        bird.x = -50;
        bird.y = canvas.height * 0.2 + Math.random() * canvas.height * 0.2;
      }

      const wingPos = Math.sin(bird.wingPhase) * 0.5;

      ctx.strokeStyle = darkMode ? '#3a3a4a' : '#5a5a6a';
      ctx.lineWidth = 1;

      ctx.beginPath();
      ctx.moveTo(bird.x - bird.size, bird.y + wingPos * bird.size);
      ctx.quadraticCurveTo(bird.x, bird.y - bird.size * 0.3, bird.x + bird.size, bird.y + wingPos * bird.size);
      ctx.stroke();
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawSky();

      // Draw mountain layers back to front
      mountainsRef.current.forEach((layer) => drawMountainLayer(layer, time));

      // Draw mist
      mistRef.current.forEach((mist) => drawMist(mist));

      // Draw birds
      birdsRef.current.forEach((bird) => drawBird(bird, time));

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
