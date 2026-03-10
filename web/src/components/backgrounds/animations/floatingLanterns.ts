/**
 * Floating Lanterns Animation
 *
 * Paper lanterns rising into a twilight sky,
 * each emitting a warm amber glow like Yi Peng festival.
 */

import { useRef } from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

interface Lantern {
  x: number;
  y: number;
  size: number;
  glowSize: number;
  speedY: number;
  swayPhase: number;
  swaySpeed: number;
  swayAmount: number;
  rotation: number;
  rotationSpeed: number;
  brightness: number;
  flickerPhase: number;
  flickerSpeed: number;
}

interface Star {
  x: number;
  y: number;
  size: number;
  twinklePhase: number;
  twinkleSpeed: number;
}

interface Spark {
  x: number;
  y: number;
  speedX: number;
  speedY: number;
  life: number;
  size: number;
}

export function useFloatingLanterns(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  enabled: boolean
) {
  const lanternsRef = useRef<Lantern[]>([]);
  const starsRef = useRef<Star[]>([]);
  const sparksRef = useRef<Spark[]>([]);

  const createLantern = (w: number, h: number, initialSpawn: boolean): Lantern => ({
    x: Math.random() * w,
    y: initialSpawn ? Math.random() * h : h + 50 + Math.random() * 100,
    size: 25 + Math.random() * 20,
    glowSize: 40 + Math.random() * 30,
    speedY: 0.3 + Math.random() * 0.4,
    swayPhase: Math.random() * Math.PI * 2,
    swaySpeed: 0.008 + Math.random() * 0.008,
    swayAmount: 15 + Math.random() * 20,
    rotation: (Math.random() - 0.5) * 0.3,
    rotationSpeed: (Math.random() - 0.5) * 0.002,
    brightness: 0.7 + Math.random() * 0.3,
    flickerPhase: Math.random() * Math.PI * 2,
    flickerSpeed: 0.1 + Math.random() * 0.1,
  });

  useCanvasAnimation(canvasRef, darkMode, opacity, enabled, {
    init: (_ctx, canvas) => {
      starsRef.current = [];
      for (let i = 0; i < 100; i++) {
        starsRef.current.push({
          x: Math.random() * canvas.width,
          y: Math.random() * canvas.height * 0.7,
          size: 0.5 + Math.random() * 1.5,
          twinklePhase: Math.random() * Math.PI * 2,
          twinkleSpeed: 0.02 + Math.random() * 0.03,
        });
      }

      lanternsRef.current = [];
      for (let i = 0; i < 12; i++) {
        lanternsRef.current.push(createLantern(canvas.width, canvas.height, true));
      }

      sparksRef.current = [];
    },
    draw: (ctx, canvas) => {
      const currentWidth = canvas.width;
      const currentHeight = canvas.height;
      ctx.clearRect(0, 0, currentWidth, currentHeight);

      const skyGradient = ctx.createLinearGradient(0, 0, 0, currentHeight);
      if (darkMode) {
        skyGradient.addColorStop(0, '#0a0a18');
        skyGradient.addColorStop(0.3, '#151028');
        skyGradient.addColorStop(0.6, '#1a1530');
        skyGradient.addColorStop(0.85, '#251535');
        skyGradient.addColorStop(1, '#301838');
      } else {
        skyGradient.addColorStop(0, '#1a1a30');
        skyGradient.addColorStop(0.3, '#252040');
        skyGradient.addColorStop(0.6, '#302545');
        skyGradient.addColorStop(0.85, '#402550');
        skyGradient.addColorStop(1, '#502855');
      }
      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, currentWidth, currentHeight);

      const horizonGlow = ctx.createRadialGradient(
        currentWidth * 0.5, currentHeight * 1.2, 0,
        currentWidth * 0.5, currentHeight * 1.2, currentHeight * 0.8
      );
      horizonGlow.addColorStop(0, 'rgba(80, 40, 60, 0.3)');
      horizonGlow.addColorStop(0.5, 'rgba(60, 30, 50, 0.15)');
      horizonGlow.addColorStop(1, 'rgba(40, 20, 40, 0)');
      ctx.fillStyle = horizonGlow;
      ctx.fillRect(0, 0, currentWidth, currentHeight);

      starsRef.current.forEach((star) => {
        star.twinklePhase += star.twinkleSpeed;
        const twinkle = 0.3 + Math.sin(star.twinklePhase) * 0.5;

        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${twinkle})`;
        ctx.fill();
      });

      sparksRef.current = sparksRef.current.filter((spark) => {
        spark.x += spark.speedX;
        spark.y += spark.speedY;
        spark.life -= 0.02;

        if (spark.life <= 0) return false;

        ctx.beginPath();
        ctx.arc(spark.x, spark.y, spark.size * spark.life, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 200, 100, ${spark.life * 0.8})`;
        ctx.fill();

        return true;
      });

      lanternsRef.current.forEach((lantern, index) => {
        lantern.swayPhase += lantern.swaySpeed;
        lantern.flickerPhase += lantern.flickerSpeed;
        lantern.rotation += lantern.rotationSpeed;

        lantern.x += Math.cos(lantern.swayPhase) * 0.3;
        lantern.y -= lantern.speedY;

        if (Math.random() < 0.01) {
          sparksRef.current.push({
            x: lantern.x,
            y: lantern.y + lantern.size * 0.8,
            speedX: (Math.random() - 0.5) * 0.5,
            speedY: Math.random() * 0.5 + 0.2,
            life: 1,
            size: 1.5 + Math.random(),
          });
        }

        // Draw lantern
        const flicker = 0.85 + Math.sin(lantern.flickerPhase) * 0.15;
        const currentBrightness = lantern.brightness * flicker;

        ctx.save();
        ctx.translate(lantern.x, lantern.y);
        ctx.rotate(lantern.rotation);

        const outerGlow = ctx.createRadialGradient(0, 0, 0, 0, 0, lantern.glowSize * 1.5);
        outerGlow.addColorStop(0, `rgba(255, 180, 80, ${currentBrightness * 0.3})`);
        outerGlow.addColorStop(0.3, `rgba(255, 140, 50, ${currentBrightness * 0.15})`);
        outerGlow.addColorStop(0.6, `rgba(255, 100, 30, ${currentBrightness * 0.05})`);
        outerGlow.addColorStop(1, 'rgba(255, 80, 20, 0)');
        ctx.beginPath();
        ctx.arc(0, 0, lantern.glowSize * 1.5, 0, Math.PI * 2);
        ctx.fillStyle = outerGlow;
        ctx.fill();

        const s = lantern.size;
        const bodyGlow = ctx.createRadialGradient(0, 0, 0, 0, 0, s);
        bodyGlow.addColorStop(0, `rgba(255, 220, 150, ${currentBrightness})`);
        bodyGlow.addColorStop(0.5, `rgba(255, 180, 100, ${currentBrightness * 0.9})`);
        bodyGlow.addColorStop(0.8, `rgba(255, 140, 60, ${currentBrightness * 0.7})`);
        bodyGlow.addColorStop(1, `rgba(200, 100, 40, ${currentBrightness * 0.5})`);
        ctx.beginPath();
        ctx.ellipse(0, 0, s * 0.6, s * 0.8, 0, 0, Math.PI * 2);
        ctx.fillStyle = bodyGlow;
        ctx.fill();

        ctx.strokeStyle = `rgba(180, 100, 50, ${currentBrightness * 0.4})`;
        ctx.lineWidth = 1;
        for (let i = -2; i <= 2; i++) {
          ctx.beginPath();
          ctx.moveTo(i * s * 0.15, -s * 0.75);
          ctx.quadraticCurveTo(i * s * 0.2, 0, i * s * 0.15, s * 0.75);
          ctx.stroke();
        }

        ctx.beginPath();
        ctx.ellipse(0, -s * 0.7, s * 0.3, s * 0.1, 0, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(120, 70, 40, ${currentBrightness * 0.6})`;
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.beginPath();
        ctx.ellipse(0, s * 0.7, s * 0.25, s * 0.08, 0, 0, Math.PI * 2);
        ctx.stroke();

        const flameGlow = ctx.createRadialGradient(0, s * 0.1, 0, 0, s * 0.1, s * 0.3);
        flameGlow.addColorStop(0, `rgba(255, 255, 220, ${currentBrightness})`);
        flameGlow.addColorStop(0.5, `rgba(255, 220, 150, ${currentBrightness * 0.5})`);
        flameGlow.addColorStop(1, 'rgba(255, 180, 100, 0)');
        ctx.beginPath();
        ctx.arc(0, s * 0.1, s * 0.3, 0, Math.PI * 2);
        ctx.fillStyle = flameGlow;
        ctx.fill();

        ctx.restore();

        if (lantern.y < -lantern.size * 2) {
          lanternsRef.current[index] = createLantern(currentWidth, currentHeight, false);
        }
      });

      if (Math.random() < 0.005 && lanternsRef.current.length < 20) {
        lanternsRef.current.push(createLantern(currentWidth, currentHeight, false));
      }

      if (sparksRef.current.length > 50) {
        sparksRef.current = sparksRef.current.slice(-40);
      }
    },
  });
}
