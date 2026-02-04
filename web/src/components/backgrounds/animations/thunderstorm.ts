/**
 * Thunderstorm Animation
 *
 * Dark rolling clouds with dramatic lightning flashes,
 * heavy rain, and atmospheric thunder effects.
 */

import { useEffect, useRef } from 'react';

interface RainDrop {
  x: number;
  y: number;
  length: number;
  speed: number;
  windOffset: number;
}

interface Lightning {
  x: number;
  y: number;
  segments: { x: number; y: number }[];
  brightness: number;
  life: number;
  branches: { startIndex: number; segments: { x: number; y: number }[] }[];
}

interface Cloud {
  x: number;
  y: number;
  width: number;
  height: number;
  speed: number;
  darkness: number;
}

interface Ripple {
  x: number;
  y: number;
  radius: number;
  opacity: number;
}

export function useThunderstorm(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  enabled: boolean
) {
  const rainRef = useRef<RainDrop[]>([]);
  const cloudsRef = useRef<Cloud[]>([]);
  const lightningRef = useRef<Lightning | null>(null);
  const ripplesRef = useRef<Ripple[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef(0);
  const flashRef = useRef(0);
  const nextLightningRef = useRef(Math.random() * 3 + 2);
  const windRef = useRef(0);
  const windTargetRef = useRef(0);

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

    // Initialize rain
    rainRef.current = [];
    for (let i = 0; i < 300; i++) {
      rainRef.current.push(createRainDrop(width, height, true));
    }

    // Initialize clouds
    cloudsRef.current = [];
    for (let i = 0; i < 8; i++) {
      cloudsRef.current.push({
        x: Math.random() * width * 1.5 - width * 0.25,
        y: Math.random() * height * 0.3,
        width: 200 + Math.random() * 300,
        height: 60 + Math.random() * 80,
        speed: 0.2 + Math.random() * 0.3,
        darkness: 0.3 + Math.random() * 0.4,
      });
    }

    ripplesRef.current = [];

    function createRainDrop(w: number, h: number, initialSpawn: boolean): RainDrop {
      return {
        x: Math.random() * w * 1.5 - w * 0.25,
        y: initialSpawn ? Math.random() * h : -20 - Math.random() * 100,
        length: 15 + Math.random() * 25,
        speed: 15 + Math.random() * 10,
        windOffset: 0,
      };
    }

    function createLightning(startX: number, startY: number, endY: number): Lightning {
      const segments: { x: number; y: number }[] = [{ x: startX, y: startY }];
      let currentX = startX;
      let currentY = startY;
      const segmentLength = 20 + Math.random() * 30;
      const branches: { startIndex: number; segments: { x: number; y: number }[] }[] = [];

      while (currentY < endY) {
        currentX += (Math.random() - 0.5) * 60;
        currentY += segmentLength + Math.random() * 20;
        segments.push({ x: currentX, y: Math.min(currentY, endY) });

        // Chance to create a branch
        if (Math.random() < 0.3 && segments.length > 2) {
          const branchSegments: { x: number; y: number }[] = [];
          let bx = currentX;
          let by = currentY;
          const branchDirection = Math.random() > 0.5 ? 1 : -1;
          const branchLength = 2 + Math.floor(Math.random() * 4);

          for (let i = 0; i < branchLength; i++) {
            bx += branchDirection * (20 + Math.random() * 30);
            by += 15 + Math.random() * 20;
            branchSegments.push({ x: bx, y: by });
          }

          branches.push({ startIndex: segments.length - 1, segments: branchSegments });
        }
      }

      return {
        x: startX,
        y: startY,
        segments,
        brightness: 1,
        life: 1,
        branches,
      };
    }

    function drawLightning(ctx: CanvasRenderingContext2D, lightning: Lightning) {
      const alpha = lightning.life;

      // Main bolt
      ctx.beginPath();
      ctx.moveTo(lightning.segments[0].x, lightning.segments[0].y);
      for (let i = 1; i < lightning.segments.length; i++) {
        ctx.lineTo(lightning.segments[i].x, lightning.segments[i].y);
      }
      ctx.strokeStyle = `rgba(255, 255, 255, ${alpha})`;
      ctx.lineWidth = 3;
      ctx.stroke();

      // Glow effect
      ctx.strokeStyle = `rgba(200, 220, 255, ${alpha * 0.5})`;
      ctx.lineWidth = 8;
      ctx.stroke();

      ctx.strokeStyle = `rgba(150, 180, 255, ${alpha * 0.3})`;
      ctx.lineWidth = 15;
      ctx.stroke();

      // Draw branches
      lightning.branches.forEach((branch) => {
        const startPoint = lightning.segments[branch.startIndex];
        ctx.beginPath();
        ctx.moveTo(startPoint.x, startPoint.y);
        branch.segments.forEach((seg) => {
          ctx.lineTo(seg.x, seg.y);
        });
        ctx.strokeStyle = `rgba(255, 255, 255, ${alpha * 0.7})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        ctx.strokeStyle = `rgba(200, 220, 255, ${alpha * 0.3})`;
        ctx.lineWidth = 4;
        ctx.stroke();
      });
    }

    const animate = () => {
      const currentWidth = canvas.width;
      const currentHeight = canvas.height;
      ctx.clearRect(0, 0, currentWidth, currentHeight);
      timeRef.current += 0.016;

      // Update wind
      if (Math.random() < 0.01) {
        windTargetRef.current = (Math.random() - 0.3) * 8;
      }
      windRef.current += (windTargetRef.current - windRef.current) * 0.02;

      // Sky gradient with flash effect
      const flashIntensity = flashRef.current;
      const skyGradient = ctx.createLinearGradient(0, 0, 0, currentHeight);

      if (darkMode) {
        const flash = flashIntensity * 0.4;
        skyGradient.addColorStop(0, `rgb(${15 + flash * 100}, ${15 + flash * 100}, ${25 + flash * 80})`);
        skyGradient.addColorStop(0.4, `rgb(${20 + flash * 80}, ${20 + flash * 80}, ${35 + flash * 60})`);
        skyGradient.addColorStop(0.7, `rgb(${25 + flash * 60}, ${25 + flash * 60}, ${40 + flash * 50})`);
        skyGradient.addColorStop(1, `rgb(${20 + flash * 50}, ${25 + flash * 50}, ${35 + flash * 40})`);
      } else {
        const flash = flashIntensity * 0.5;
        skyGradient.addColorStop(0, `rgb(${40 + flash * 150}, ${45 + flash * 150}, ${60 + flash * 120})`);
        skyGradient.addColorStop(0.4, `rgb(${50 + flash * 120}, ${55 + flash * 120}, ${70 + flash * 100})`);
        skyGradient.addColorStop(0.7, `rgb(${55 + flash * 100}, ${60 + flash * 100}, ${75 + flash * 80})`);
        skyGradient.addColorStop(1, `rgb(${45 + flash * 80}, ${50 + flash * 80}, ${60 + flash * 60})`);
      }

      ctx.fillStyle = skyGradient;
      ctx.fillRect(0, 0, currentWidth, currentHeight);

      // Decay flash
      flashRef.current *= 0.9;

      // Draw clouds
      cloudsRef.current.forEach((cloud) => {
        cloud.x += cloud.speed + windRef.current * 0.1;

        // Wrap around
        if (cloud.x > currentWidth + cloud.width) {
          cloud.x = -cloud.width;
        }

        // Cloud shape using multiple ellipses
        const baseAlpha = cloud.darkness + flashRef.current * 0.3;

        ctx.fillStyle = darkMode
          ? `rgba(20, 20, 30, ${baseAlpha})`
          : `rgba(60, 65, 80, ${baseAlpha})`;

        // Main cloud body
        ctx.beginPath();
        ctx.ellipse(cloud.x, cloud.y, cloud.width * 0.5, cloud.height, 0, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.ellipse(cloud.x - cloud.width * 0.3, cloud.y + 10, cloud.width * 0.35, cloud.height * 0.8, 0, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.ellipse(cloud.x + cloud.width * 0.3, cloud.y + 5, cloud.width * 0.4, cloud.height * 0.9, 0, 0, Math.PI * 2);
        ctx.fill();
      });

      // Check for lightning trigger
      nextLightningRef.current -= 0.016;
      if (nextLightningRef.current <= 0) {
        const startX = Math.random() * currentWidth;
        lightningRef.current = createLightning(startX, 50, currentHeight * 0.7);
        flashRef.current = 1;
        nextLightningRef.current = 2 + Math.random() * 5;
      }

      // Update and draw lightning
      if (lightningRef.current) {
        lightningRef.current.life -= 0.05;
        if (lightningRef.current.life > 0) {
          drawLightning(ctx, lightningRef.current);
        } else {
          lightningRef.current = null;
        }
      }

      // Draw rain
      ctx.strokeStyle = darkMode
        ? `rgba(150, 170, 200, ${0.4 + flashRef.current * 0.3})`
        : `rgba(180, 200, 230, ${0.5 + flashRef.current * 0.3})`;
      ctx.lineWidth = 1.5;

      rainRef.current.forEach((drop, index) => {
        drop.windOffset = windRef.current;
        drop.y += drop.speed;
        drop.x += drop.windOffset;

        ctx.beginPath();
        ctx.moveTo(drop.x, drop.y);
        ctx.lineTo(drop.x + drop.windOffset * 2, drop.y + drop.length);
        ctx.stroke();

        // Reset when off screen
        if (drop.y > currentHeight) {
          // Create ripple
          if (Math.random() < 0.3) {
            ripplesRef.current.push({
              x: drop.x + drop.windOffset * 2,
              y: currentHeight - 5 - Math.random() * 20,
              radius: 1,
              opacity: 0.5,
            });
          }
          rainRef.current[index] = createRainDrop(currentWidth, currentHeight, false);
        }
      });

      // Update and draw ripples
      ripplesRef.current = ripplesRef.current.filter((ripple) => {
        ripple.radius += 0.8;
        ripple.opacity -= 0.02;

        if (ripple.opacity <= 0) return false;

        ctx.beginPath();
        ctx.arc(ripple.x, ripple.y, ripple.radius, 0, Math.PI * 2);
        ctx.strokeStyle = darkMode
          ? `rgba(100, 120, 150, ${ripple.opacity})`
          : `rgba(150, 170, 200, ${ripple.opacity})`;
        ctx.lineWidth = 1;
        ctx.stroke();

        return true;
      });

      // Limit ripples
      if (ripplesRef.current.length > 50) {
        ripplesRef.current = ripplesRef.current.slice(-40);
      }

      // Ground/horizon line with wet reflection
      const groundY = currentHeight * 0.95;
      const groundGradient = ctx.createLinearGradient(0, groundY, 0, currentHeight);
      if (darkMode) {
        groundGradient.addColorStop(0, `rgba(15, 20, 30, ${0.8 + flashRef.current * 0.2})`);
        groundGradient.addColorStop(1, `rgba(10, 15, 25, ${0.9 + flashRef.current * 0.1})`);
      } else {
        groundGradient.addColorStop(0, `rgba(40, 50, 65, ${0.7 + flashRef.current * 0.3})`);
        groundGradient.addColorStop(1, `rgba(30, 40, 55, ${0.8 + flashRef.current * 0.2})`);
      }
      ctx.fillStyle = groundGradient;
      ctx.fillRect(0, groundY, currentWidth, currentHeight - groundY);

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
