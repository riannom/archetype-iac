/**
 * Fireworks Animation
 *
 * Colorful fireworks bursting in the night sky.
 * Rockets launch, explode into particles, and fade with trails.
 */

import { useEffect, useRef } from 'react';

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  color: string;
  size: number;
  life: number;
  maxLife: number;
  trail: { x: number; y: number; opacity: number }[];
  gravity: number;
  friction: number;
}

interface Rocket {
  x: number;
  y: number;
  targetY: number;
  speed: number;
  color: string;
  trail: { x: number; y: number; opacity: number }[];
  hasExploded: boolean;
}

interface Sparkle {
  x: number;
  y: number;
  size: number;
  opacity: number;
  phase: number;
}

export function useFireworks(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const particlesRef = useRef<Particle[]>([]);
  const rocketsRef = useRef<Rocket[]>([]);
  const sparklesRef = useRef<Sparkle[]>([]);
  const animationRef = useRef<number>(0);
  const timeRef = useRef<number>(0);
  const lastLaunchRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      initializeSparkles();
    };

    const initializeSparkles = () => {
      // Background stars/sparkles
      sparklesRef.current = [];
      for (let i = 0; i < 50; i++) {
        sparklesRef.current.push({
          x: Math.random() * canvas.width,
          y: Math.random() * canvas.height * 0.7,
          size: 1 + Math.random() * 2,
          opacity: 0.3 + Math.random() * 0.5,
          phase: Math.random() * Math.PI * 2,
        });
      }
    };

    const colors = [
      '#FF6B6B', // Red
      '#4ECDC4', // Teal
      '#FFE66D', // Yellow
      '#FF8C42', // Orange
      '#A8E6CF', // Mint
      '#DDA0DD', // Plum
      '#87CEEB', // Sky blue
      '#FFB6C1', // Pink
      '#F0E68C', // Khaki
      '#98D8C8', // Seafoam
      '#FF1493', // Deep Pink
      '#00FF7F', // Spring Green
      '#FFD700', // Gold
      '#FF4500', // Orange Red
      '#DA70D6', // Orchid
      '#40E0D0', // Turquoise
      '#FF69B4', // Hot Pink
      '#7B68EE', // Medium Slate Blue
    ];

    const launchRocket = () => {
      const x = canvas.width * 0.2 + Math.random() * canvas.width * 0.6;
      rocketsRef.current.push({
        x,
        y: canvas.height,
        targetY: canvas.height * 0.15 + Math.random() * canvas.height * 0.35,
        speed: 4 + Math.random() * 3,
        color: colors[Math.floor(Math.random() * colors.length)],
        trail: [],
        hasExploded: false,
      });
    };

    const explodeRocket = (rocket: Rocket) => {
      const baseColor = rocket.color;

      // Create explosion pattern - more variety!
      const pattern = Math.floor(Math.random() * 10);

      // Different patterns have different particle counts
      let particleCount: number;
      switch (pattern) {
        case 7: // Chrysanthemum - lots of thin trails
          particleCount = 120 + Math.floor(Math.random() * 40);
          break;
        case 8: // Peony - large and fluffy
          particleCount = 100 + Math.floor(Math.random() * 50);
          break;
        case 9: // Crossette - splits into clusters
          particleCount = 40;
          break;
        default:
          particleCount = 60 + Math.floor(Math.random() * 40);
      }

      for (let i = 0; i < particleCount; i++) {
        let angle: number;
        let speed: number;
        let gravity = 0.03;
        let friction = 0.98;
        let maxLife = 80 + Math.random() * 40;
        let size = 2 + Math.random() * 2;
        let particleColor = Math.random() > 0.3 ? baseColor : colors[Math.floor(Math.random() * colors.length)];

        switch (pattern) {
          case 0: // Circle burst
            angle = (i / particleCount) * Math.PI * 2;
            speed = 3 + Math.random() * 3;
            break;

          case 1: // Double ring
            angle = (i / particleCount) * Math.PI * 2;
            speed = i % 2 === 0 ? 3 + Math.random() * 2 : 5 + Math.random() * 2;
            break;

          case 2: // Scatter spray
            angle = Math.random() * Math.PI * 2;
            speed = 1 + Math.random() * 5;
            break;

          case 3: // Willow (drooping graceful trails)
            angle = (i / particleCount) * Math.PI * 2;
            speed = 2 + Math.random() * 2.5;
            gravity = 0.06;
            maxLife = 120 + Math.random() * 60;
            friction = 0.99;
            break;

          case 4: // Heart shape
            const t = (i / particleCount) * Math.PI * 2;
            const heartX = 16 * Math.pow(Math.sin(t), 3);
            const heartY = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t);
            angle = Math.atan2(-heartY, heartX);
            speed = Math.sqrt(heartX * heartX + heartY * heartY) * 0.2 + Math.random() * 0.5;
            particleColor = ['#FF69B4', '#FF1493', '#FF6B6B', '#FFB6C1'][Math.floor(Math.random() * 4)];
            break;

          case 5: // Star (5 points)
            const starPoints = 5;
            const starAngle = (i / particleCount) * Math.PI * 2;
            const starRadius = (i % 2 === 0) ? 1 : 0.4;
            angle = starAngle;
            speed = (3 + Math.random() * 2) * starRadius;
            particleColor = ['#FFD700', '#FFE66D', '#FFFFFF'][Math.floor(Math.random() * 3)];
            break;

          case 6: // Spiral
            angle = (i / particleCount) * Math.PI * 6 + i * 0.1;
            speed = 1.5 + (i / particleCount) * 4;
            friction = 0.97;
            break;

          case 7: // Chrysanthemum (many thin trails)
            angle = (i / particleCount) * Math.PI * 2 + (Math.random() - 0.5) * 0.2;
            speed = 2 + Math.random() * 4;
            size = 1 + Math.random() * 1.5;
            maxLife = 100 + Math.random() * 50;
            gravity = 0.04;
            friction = 0.985;
            break;

          case 8: // Peony (large fluffy burst)
            angle = Math.random() * Math.PI * 2;
            speed = 1 + Math.random() * 4.5;
            size = 2.5 + Math.random() * 2.5;
            maxLife = 90 + Math.random() * 50;
            gravity = 0.025;
            break;

          case 9: // Crossette (splits into small clusters)
            angle = (i / particleCount) * Math.PI * 2;
            speed = 4 + Math.random() * 2;
            friction = 0.96;
            maxLife = 50;
            // These particles will spawn more when they die
            break;

          default:
            angle = Math.random() * Math.PI * 2;
            speed = 2 + Math.random() * 4;
        }

        particlesRef.current.push({
          x: rocket.x,
          y: rocket.y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          color: particleColor,
          size,
          life: 1,
          maxLife,
          trail: [],
          gravity,
          friction,
        });
      }

      // Add center flash (brighter for some patterns)
      const flashCount = [4, 5, 7, 8].includes(pattern) ? 12 : 8;
      for (let i = 0; i < flashCount; i++) {
        particlesRef.current.push({
          x: rocket.x,
          y: rocket.y,
          vx: (Math.random() - 0.5) * 2,
          vy: (Math.random() - 0.5) * 2,
          color: '#FFFFFF',
          size: 4 + Math.random() * 3,
          life: 1,
          maxLife: 20,
          trail: [],
          gravity: 0.01,
          friction: 0.95,
        });
      }

      // Special: Crossette secondary explosions (add delayed mini-bursts)
      if (pattern === 9) {
        setTimeout(() => {
          // Create 4-6 mini explosions at different points
          for (let burst = 0; burst < 4 + Math.floor(Math.random() * 3); burst++) {
            const burstAngle = Math.random() * Math.PI * 2;
            const burstDist = 40 + Math.random() * 30;
            const burstX = rocket.x + Math.cos(burstAngle) * burstDist;
            const burstY = rocket.y + Math.sin(burstAngle) * burstDist - 20;

            for (let j = 0; j < 15; j++) {
              const miniAngle = (j / 15) * Math.PI * 2;
              const miniSpeed = 1.5 + Math.random() * 2;
              particlesRef.current.push({
                x: burstX,
                y: burstY,
                vx: Math.cos(miniAngle) * miniSpeed,
                vy: Math.sin(miniAngle) * miniSpeed,
                color: colors[Math.floor(Math.random() * colors.length)],
                size: 1.5 + Math.random() * 1.5,
                life: 1,
                maxLife: 50 + Math.random() * 30,
                trail: [],
                gravity: 0.04,
                friction: 0.97,
              });
            }
          }
        }, 200);
      }
    };

    const drawBackground = () => {
      // Night sky gradient
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
      if (darkMode) {
        gradient.addColorStop(0, '#0a0a15');
        gradient.addColorStop(0.5, '#0f0f20');
        gradient.addColorStop(1, '#151525');
      } else {
        gradient.addColorStop(0, '#1a1a2e');
        gradient.addColorStop(0.5, '#16213e');
        gradient.addColorStop(1, '#1a1a2e');
      }
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    };

    const drawSparkles = (time: number) => {
      sparklesRef.current.forEach((sparkle) => {
        const twinkle = Math.sin(time * 0.003 + sparkle.phase) * 0.3 + 0.7;
        ctx.beginPath();
        ctx.arc(sparkle.x, sparkle.y, sparkle.size * twinkle, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${sparkle.opacity * twinkle * 0.5})`;
        ctx.fill();
      });
    };

    const drawRocket = (rocket: Rocket) => {
      // Update trail
      rocket.trail.push({ x: rocket.x, y: rocket.y, opacity: 1 });
      if (rocket.trail.length > 15) {
        rocket.trail.shift();
      }

      // Draw trail
      rocket.trail.forEach((point, index) => {
        const trailOpacity = (index / rocket.trail.length) * 0.6;
        ctx.beginPath();
        ctx.arc(point.x, point.y, 2, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 200, 100, ${trailOpacity})`;
        ctx.fill();
      });

      // Draw rocket head
      ctx.beginPath();
      ctx.arc(rocket.x, rocket.y, 3, 0, Math.PI * 2);
      ctx.fillStyle = '#FFFFFF';
      ctx.fill();

      // Glow effect
      const glow = ctx.createRadialGradient(rocket.x, rocket.y, 0, rocket.x, rocket.y, 10);
      glow.addColorStop(0, 'rgba(255, 200, 100, 0.5)');
      glow.addColorStop(1, 'transparent');
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(rocket.x, rocket.y, 10, 0, Math.PI * 2);
      ctx.fill();
    };

    const drawParticle = (particle: Particle) => {
      // Update trail
      if (particle.trail.length === 0 ||
          Math.hypot(particle.x - particle.trail[particle.trail.length - 1].x,
                     particle.y - particle.trail[particle.trail.length - 1].y) > 3) {
        particle.trail.push({ x: particle.x, y: particle.y, opacity: particle.life });
        if (particle.trail.length > 8) {
          particle.trail.shift();
        }
      }

      // Draw trail
      particle.trail.forEach((point, index) => {
        const trailOpacity = (index / particle.trail.length) * point.opacity * 0.5;
        ctx.beginPath();
        ctx.arc(point.x, point.y, particle.size * 0.5, 0, Math.PI * 2);
        ctx.fillStyle = particle.color.replace(')', `, ${trailOpacity})`).replace('rgb', 'rgba');
        ctx.fill();
      });

      // Draw particle
      const alpha = particle.life;
      ctx.beginPath();
      ctx.arc(particle.x, particle.y, particle.size * alpha, 0, Math.PI * 2);

      // Color with alpha
      const colorWithAlpha = particle.color.startsWith('#')
        ? hexToRgba(particle.color, alpha)
        : particle.color.replace(')', `, ${alpha})`).replace('rgb', 'rgba');
      ctx.fillStyle = colorWithAlpha;
      ctx.fill();

      // Glow for bright particles
      if (particle.life > 0.5) {
        const glow = ctx.createRadialGradient(
          particle.x, particle.y, 0,
          particle.x, particle.y, particle.size * 2
        );
        glow.addColorStop(0, colorWithAlpha);
        glow.addColorStop(1, 'transparent');
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(particle.x, particle.y, particle.size * 2, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const hexToRgba = (hex: string, alpha: number): string => {
      const r = parseInt(hex.slice(1, 3), 16);
      const g = parseInt(hex.slice(3, 5), 16);
      const b = parseInt(hex.slice(5, 7), 16);
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      drawBackground();
      drawSparkles(time);

      // Launch new rockets periodically
      if (time - lastLaunchRef.current > 800 + Math.random() * 1500) {
        launchRocket();
        // Sometimes launch multiple
        if (Math.random() > 0.6) {
          setTimeout(() => launchRocket(), 100 + Math.random() * 200);
        }
        lastLaunchRef.current = time;
      }

      // Update and draw rockets
      rocketsRef.current = rocketsRef.current.filter((rocket) => {
        if (!rocket.hasExploded) {
          rocket.y -= rocket.speed;
          rocket.x += (Math.random() - 0.5) * 0.5; // Slight wobble

          drawRocket(rocket);

          if (rocket.y <= rocket.targetY) {
            rocket.hasExploded = true;
            explodeRocket(rocket);
            return false;
          }
          return true;
        }
        return false;
      });

      // Update and draw particles
      particlesRef.current = particlesRef.current.filter((particle) => {
        particle.vx *= particle.friction;
        particle.vy *= particle.friction;
        particle.vy += particle.gravity;

        particle.x += particle.vx;
        particle.y += particle.vy;

        particle.life -= 1 / particle.maxLife;

        if (particle.life <= 0) return false;

        drawParticle(particle);
        return true;
      });

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
