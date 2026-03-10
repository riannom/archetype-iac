/**
 * Moonlit Clouds Animation
 * Soft clouds drifting across a moonlit sky
 */

import { useRef } from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

interface Cloud {
  x: number;
  y: number;
  width: number;
  height: number;
  speed: number;
  opacity: number;
  puffs: Array<{ x: number; y: number; r: number }>;
}

interface ShootingStar {
  x: number;
  y: number;
  angle: number;
  speed: number;
  length: number;
  life: number;
  maxLife: number;
  isBig?: boolean; // Rare bigger shooting stars
}

export function useMoonlitClouds(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const cloudsRef = useRef<Cloud[]>([]);
  const shootingStarsRef = useRef<ShootingStar[]>([]);

  useCanvasAnimation(canvasRef, darkMode, opacity, active, {
    init: (_ctx, canvas) => {
      const createCloud = (startX?: number): Cloud => {
        const width = 100 + Math.random() * 150;
        const height = 40 + Math.random() * 30;
        const puffCount = 4 + Math.floor(Math.random() * 3);
        const puffs = Array.from({ length: puffCount }, (_, i) => ({
          x: (i / puffCount) * width - width / 2 + Math.random() * 20,
          y: (Math.random() - 0.5) * height * 0.5,
          r: 20 + Math.random() * 25,
        }));

        return {
          x: startX ?? -width,
          y: Math.random() * canvas.height * 0.6 + canvas.height * 0.1,
          width,
          height,
          speed: 0.15 + Math.random() * 0.2,
          opacity: 0.06 + Math.random() * 0.06,
          puffs,
        };
      };

      const cloudCount = Math.floor(canvas.width / 300) + 3;
      cloudsRef.current = Array.from({ length: cloudCount }, () =>
        createCloud(Math.random() * (canvas.width + 200) - 100)
      );
      shootingStarsRef.current = [];
    },
    draw: (ctx, canvas) => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const cloudColor = darkMode ? [180, 190, 210] : [200, 210, 230];
      const moonGlow = darkMode ? [220, 230, 250] : [240, 245, 255];
      const opacityMultiplier = opacity / 50;

      const createCloud = (startX?: number): Cloud => {
        const width = 100 + Math.random() * 150;
        const height = 40 + Math.random() * 30;
        const puffCount = 4 + Math.floor(Math.random() * 3);
        const puffs = Array.from({ length: puffCount }, (_, i) => ({
          x: (i / puffCount) * width - width / 2 + Math.random() * 20,
          y: (Math.random() - 0.5) * height * 0.5,
          r: 20 + Math.random() * 25,
        }));

        return {
          x: startX ?? -width,
          y: Math.random() * canvas.height * 0.6 + canvas.height * 0.1,
          width,
          height,
          speed: 0.15 + Math.random() * 0.2,
          opacity: 0.06 + Math.random() * 0.06,
          puffs,
        };
      };

      cloudsRef.current.forEach((cloud, i) => {
        cloud.x += cloud.speed;

        if (cloud.x > canvas.width + cloud.width) {
          cloudsRef.current[i] = createCloud();
        }

        // Draw cloud
        ctx.save();
        ctx.translate(cloud.x, cloud.y);

        cloud.puffs.forEach(puff => {
          const gradient = ctx.createRadialGradient(puff.x, puff.y, 0, puff.x, puff.y, puff.r);
          gradient.addColorStop(0, `rgba(${cloudColor[0]}, ${cloudColor[1]}, ${cloudColor[2]}, ${cloud.opacity * opacityMultiplier})`);
          gradient.addColorStop(0.6, `rgba(${cloudColor[0]}, ${cloudColor[1]}, ${cloudColor[2]}, ${cloud.opacity * opacityMultiplier * 0.5})`);
          gradient.addColorStop(1, `rgba(${cloudColor[0]}, ${cloudColor[1]}, ${cloudColor[2]}, 0)`);

          ctx.beginPath();
          ctx.arc(puff.x, puff.y, puff.r, 0, Math.PI * 2);
          ctx.fillStyle = gradient;
          ctx.fill();
        });

        const glowGradient = ctx.createLinearGradient(0, -cloud.height, 0, cloud.height * 0.5);
        glowGradient.addColorStop(0, `rgba(${moonGlow[0]}, ${moonGlow[1]}, ${moonGlow[2]}, ${cloud.opacity * opacityMultiplier * 0.3})`);
        glowGradient.addColorStop(1, 'rgba(255, 255, 255, 0)');

        ctx.beginPath();
        ctx.ellipse(0, -cloud.height * 0.3, cloud.width * 0.4, cloud.height * 0.4, 0, 0, Math.PI * 2);
        ctx.fillStyle = glowGradient;
        ctx.fill();

        ctx.restore();
      });

      if (Math.random() < 0.0015) {
        const startX = Math.random() * canvas.width;
        const startY = Math.random() * canvas.height * 0.4;
        shootingStarsRef.current.push({
          x: startX,
          y: startY,
          angle: Math.PI * 0.15 + Math.random() * Math.PI * 0.2,
          speed: 5 + Math.random() * 5,
          length: 40 + Math.random() * 80,
          life: 50,
          maxLife: 50,
        });
      }

      if (Math.random() < 0.00015) {
        const startX = Math.random() * canvas.width;
        const startY = Math.random() * canvas.height * 0.3;
        shootingStarsRef.current.push({
          x: startX,
          y: startY,
          angle: Math.PI * 0.15 + Math.random() * Math.PI * 0.15,
          speed: 8 + Math.random() * 6,
          length: 150 + Math.random() * 100,
          life: 70,
          maxLife: 70,
          isBig: true,
        });
      }

      shootingStarsRef.current = shootingStarsRef.current.filter(ss => {
        ss.x += Math.cos(ss.angle) * ss.speed;
        ss.y += Math.sin(ss.angle) * ss.speed;
        ss.life--;

        const lifeRatio = ss.life / ss.maxLife;
        const lineWidth = ss.isBig ? 4 : 2;
        const headRadius = ss.isBig ? 12 : 5;

        ctx.save();
        ctx.translate(ss.x, ss.y);
        ctx.rotate(ss.angle);

        const trailGradient = ctx.createLinearGradient(-ss.length, 0, 0, 0);
        trailGradient.addColorStop(0, 'transparent');
        trailGradient.addColorStop(0.3, `rgba(255, 255, 255, ${lifeRatio * 0.3 * opacityMultiplier})`);
        trailGradient.addColorStop(1, `rgba(255, 255, 255, ${lifeRatio * opacityMultiplier})`);

        ctx.strokeStyle = trailGradient;
        ctx.lineWidth = lineWidth;
        ctx.beginPath();
        ctx.moveTo(-ss.length, 0);
        ctx.lineTo(0, 0);
        ctx.stroke();

        if (ss.isBig) {
          const outerTrail = ctx.createLinearGradient(-ss.length * 0.7, 0, 0, 0);
          outerTrail.addColorStop(0, 'transparent');
          outerTrail.addColorStop(0.5, `rgba(200, 220, 255, ${lifeRatio * 0.15 * opacityMultiplier})`);
          outerTrail.addColorStop(1, `rgba(200, 220, 255, ${lifeRatio * 0.25 * opacityMultiplier})`);
          ctx.strokeStyle = outerTrail;
          ctx.lineWidth = 10;
          ctx.beginPath();
          ctx.moveTo(-ss.length * 0.7, 0);
          ctx.lineTo(0, 0);
          ctx.stroke();
        }

        const headGlow = ctx.createRadialGradient(0, 0, 0, 0, 0, headRadius);
        headGlow.addColorStop(0, `rgba(255, 255, 255, ${lifeRatio * opacityMultiplier})`);
        headGlow.addColorStop(0.4, `rgba(220, 240, 255, ${lifeRatio * 0.7 * opacityMultiplier})`);
        headGlow.addColorStop(1, 'transparent');
        ctx.fillStyle = headGlow;
        ctx.beginPath();
        ctx.arc(0, 0, headRadius, 0, Math.PI * 2);
        ctx.fill();

        if (ss.isBig) {
          const halo = ctx.createRadialGradient(0, 0, headRadius * 0.5, 0, 0, headRadius * 2.5);
          halo.addColorStop(0, `rgba(200, 220, 255, ${lifeRatio * 0.3 * opacityMultiplier})`);
          halo.addColorStop(1, 'transparent');
          ctx.fillStyle = halo;
          ctx.beginPath();
          ctx.arc(0, 0, headRadius * 2.5, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.restore();
        return ss.life > 0;
      });
    },
  });
}
