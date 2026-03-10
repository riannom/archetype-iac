/**
 * Sats Symbol Animation
 * Elegant floating Sats symbols with subtle glow and particle effects
 * Symbol: 3 horizontal lines with 1 vertical line through them
 */

import { useRef} from 'react';
import { useCanvasAnimation } from './useCanvasAnimation';

interface SatsSymbol {
  x: number;
  y: number;
  size: number;
  opacity: number;
  rotation: number;
  rotationSpeed: number;
  vx: number;
  vy: number;
  pulsePhase: number;
  pulseSpeed: number;
  glowIntensity: number;
}

interface Particle {
  x: number;
  y: number;
  size: number;
  opacity: number;
  vx: number;
  vy: number;
  life: number;
  maxLife: number;
}


export function useSatsSymbol(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const symbolsRef = useRef<SatsSymbol[]>([]);
  const particlesRef = useRef<Particle[]>([]);
  const sizeRef = useRef({ w: 0, h: 0 });

  useCanvasAnimation(canvasRef, darkMode, opacity, active, {

    draw: (ctx, canvas, time, _dt) => {
    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    // Initialize symbols
    const symbolCount = Math.floor((canvas.width * canvas.height) / 120000) + 5;
    symbolsRef.current = Array.from({ length: symbolCount }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      size: 30 + Math.random() * 50,
      opacity: 0.08 + Math.random() * 0.12,
      rotation: Math.random() * Math.PI * 0.1 - Math.PI * 0.05,
      rotationSpeed: (Math.random() - 0.5) * 0.0003,
      vx: (Math.random() - 0.5) * 0.15,
      vy: -0.1 - Math.random() * 0.15,
      pulsePhase: Math.random() * Math.PI * 2,
      pulseSpeed: 0.01 + Math.random() * 0.01,
      glowIntensity: 0.3 + Math.random() * 0.4,
    }));

    particlesRef.current = [];

    // Draw the Sats symbol (3 horizontal lines + 1 vertical line)
    const drawSatsSymbol = (symbol: SatsSymbol, _time: number) => {
      ctx.save();
      ctx.translate(symbol.x, symbol.y);
      ctx.rotate(symbol.rotation);

      const pulse = Math.sin(symbol.pulsePhase) * 0.15 + 1;
      const size = symbol.size * pulse;
      const opacityMultiplier = opacity / 50;
      const baseOpacity = symbol.opacity * opacityMultiplier;

      // Colors
      const primaryColor = darkMode
        ? `rgba(255, 180, 50, ${baseOpacity})`
        : `rgba(230, 140, 20, ${baseOpacity})`;
      const glowColor = darkMode
        ? `rgba(255, 200, 100, ${baseOpacity * symbol.glowIntensity * 0.5})`
        : `rgba(255, 180, 60, ${baseOpacity * symbol.glowIntensity * 0.4})`;

      // Draw glow
      const glowRadius = size * 0.8;
      const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, glowRadius);
      glow.addColorStop(0, glowColor);
      glow.addColorStop(0.5, `rgba(255, 180, 50, ${baseOpacity * 0.1})`);
      glow.addColorStop(1, 'transparent');
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(0, 0, glowRadius, 0, Math.PI * 2);
      ctx.fill();

      // Draw the Sats symbol: 3 horizontal lines with 1 vertical line through them
      ctx.strokeStyle = primaryColor;
      ctx.fillStyle = primaryColor;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';

      const h = size * 0.6;
      const w = size * 0.4;
      const lineWidth = size * 0.08;
      const stemExtend = h * 0.25;

      ctx.lineWidth = lineWidth;

      // Three horizontal lines
      // Top line
      ctx.beginPath();
      ctx.moveTo(-w * 0.5, -h * 0.35);
      ctx.lineTo(w * 0.5, -h * 0.35);
      ctx.stroke();

      // Middle line
      ctx.beginPath();
      ctx.moveTo(-w * 0.5, 0);
      ctx.lineTo(w * 0.5, 0);
      ctx.stroke();

      // Bottom line
      ctx.beginPath();
      ctx.moveTo(-w * 0.5, h * 0.35);
      ctx.lineTo(w * 0.5, h * 0.35);
      ctx.stroke();

      // Single vertical line through all three horizontals (with extension above/below)
      ctx.beginPath();
      ctx.moveTo(0, -h * 0.35 - stemExtend);  // Top extension
      ctx.lineTo(0, h * 0.35 + stemExtend);   // Bottom extension
      ctx.stroke();

      ctx.restore();
    };

    // Spawn particles occasionally
    const maybeSpawnParticle = (symbol: SatsSymbol) => {
      if (Math.random() < 0.02 && particlesRef.current.length < 50) {
        const angle = Math.random() * Math.PI * 2;
        const distance = symbol.size * 0.3;
        particlesRef.current.push({
          x: symbol.x + Math.cos(angle) * distance,
          y: symbol.y + Math.sin(angle) * distance,
          size: 1 + Math.random() * 2,
          opacity: 0.3 + Math.random() * 0.3,
          vx: (Math.random() - 0.5) * 0.5,
          vy: -0.3 - Math.random() * 0.5,
          life: 1,
          maxLife: 60 + Math.random() * 60,
        });
      }
    };

    // Draw a particle
    const drawParticle = (particle: Particle) => {
      const opacityMultiplier = opacity / 50;
      const lifeRatio = particle.life / particle.maxLife;
      const alpha = particle.opacity * lifeRatio * opacityMultiplier;

      const color = darkMode
        ? `rgba(255, 200, 100, ${alpha})`
        : `rgba(255, 170, 50, ${alpha})`;

      ctx.beginPath();
      ctx.arc(particle.x, particle.y, particle.size * lifeRatio, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
    };


      const w = canvas.width;
      const h = canvas.height;
      if (sizeRef.current.w !== w || sizeRef.current.h !== h) {
        sizeRef.current = { w, h };
      }


      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // Update and draw symbols
      symbolsRef.current.forEach((symbol) => {
        // Update position
        symbol.x += symbol.vx;
        symbol.y += symbol.vy;
        symbol.rotation += symbol.rotationSpeed;
        symbol.pulsePhase += symbol.pulseSpeed;

        // Wrap around screen
        const margin = symbol.size;
        if (symbol.y < -margin) {
          symbol.y = canvas.height + margin * 0.5;
          symbol.x = Math.random() * canvas.width;
        }
        if (symbol.x < -margin) symbol.x = canvas.width + margin * 0.5;
        if (symbol.x > canvas.width + margin) symbol.x = -margin * 0.5;

        maybeSpawnParticle(symbol);
        drawSatsSymbol(symbol, time);
      });

      // Update and draw particles
      particlesRef.current = particlesRef.current.filter((particle) => {
        particle.x += particle.vx;
        particle.y += particle.vy;
        particle.vy -= 0.005; // Slight upward drift
        particle.life--;

        if (particle.life <= 0) return false;

        drawParticle(particle);
        return true;
      });

    },
  });
}
