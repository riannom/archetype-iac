/**
 * Ice Crystals Animation
 * Frost patterns forming and dissolving on the screen.
 * Delicate crystalline structures that grow from seed points.
 */

import { useEffect, useRef } from 'react';

interface Crystal {
  x: number;
  y: number;
  branches: Branch[];
  growth: number;
  maxGrowth: number;
  opacity: number;
  fading: boolean;
  rotation: number;
  scale: number;
}

interface Branch {
  angle: number;
  length: number;
  maxLength: number;
  subBranches: SubBranch[];
  grown: boolean;
}

interface SubBranch {
  startRatio: number;
  angle: number;
  length: number;
  maxLength: number;
}

interface FrostParticle {
  x: number;
  y: number;
  size: number;
  opacity: number;
  drift: number;
  phase: number;
}

export function useIceCrystals(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const crystalsRef = useRef<Crystal[]>([]);
  const particlesRef = useRef<FrostParticle[]>([]);
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
      initializeParticles();
    };

    const initializeParticles = () => {
      particlesRef.current = [];
      const particleCount = Math.floor((canvas.width * canvas.height) / 5000);

      for (let i = 0; i < particleCount; i++) {
        particlesRef.current.push({
          x: Math.random() * canvas.width,
          y: Math.random() * canvas.height,
          size: 1 + Math.random() * 2,
          opacity: 0.2 + Math.random() * 0.4,
          drift: (Math.random() - 0.5) * 0.3,
          phase: Math.random() * Math.PI * 2,
        });
      }
    };

    const createCrystal = (): Crystal => {
      const branchCount = 6; // Hexagonal symmetry like real ice
      const branches: Branch[] = [];

      for (let i = 0; i < branchCount; i++) {
        const angle = (i / branchCount) * Math.PI * 2;
        const maxLength = 40 + Math.random() * 60;

        const subBranches: SubBranch[] = [];
        const subCount = 2 + Math.floor(Math.random() * 3);

        for (let j = 0; j < subCount; j++) {
          const startRatio = 0.3 + (j / subCount) * 0.5;
          subBranches.push({
            startRatio,
            angle: (Math.random() > 0.5 ? 1 : -1) * (Math.PI / 6 + Math.random() * Math.PI / 6),
            length: 0,
            maxLength: maxLength * (0.3 + Math.random() * 0.3) * (1 - startRatio),
          });
        }

        branches.push({
          angle,
          length: 0,
          maxLength,
          subBranches,
          grown: false,
        });
      }

      return {
        x: 50 + Math.random() * (canvas.width - 100),
        y: 50 + Math.random() * (canvas.height - 100),
        branches,
        growth: 0,
        maxGrowth: 150 + Math.random() * 100,
        opacity: 0,
        fading: false,
        rotation: Math.random() * Math.PI / 6,
        scale: 0.7 + Math.random() * 0.6,
      };
    };

    const drawCrystalBranch = (
      ctx: CanvasRenderingContext2D,
      startX: number,
      startY: number,
      angle: number,
      length: number,
      lineWidth: number,
      alpha: number,
      opacityMult: number
    ) => {
      if (length <= 0) return;

      const endX = startX + Math.cos(angle) * length;
      const endY = startY + Math.sin(angle) * length;

      // Create gradient along branch
      const gradient = ctx.createLinearGradient(startX, startY, endX, endY);
      const crystalColor = darkMode
        ? { r: 180, g: 220, b: 255 }
        : { r: 150, g: 200, b: 240 };

      gradient.addColorStop(0, `rgba(${crystalColor.r}, ${crystalColor.g}, ${crystalColor.b}, ${alpha * opacityMult})`);
      gradient.addColorStop(1, `rgba(${crystalColor.r + 30}, ${crystalColor.g + 20}, ${crystalColor.b}, ${alpha * 0.6 * opacityMult})`);

      ctx.strokeStyle = gradient;
      ctx.lineWidth = lineWidth;
      ctx.lineCap = 'round';

      ctx.beginPath();
      ctx.moveTo(startX, startY);
      ctx.lineTo(endX, endY);
      ctx.stroke();

      return { endX, endY };
    };

    const drawCrystal = (crystal: Crystal, opacityMult: number) => {
      ctx.save();
      ctx.translate(crystal.x, crystal.y);
      ctx.rotate(crystal.rotation);
      ctx.scale(crystal.scale, crystal.scale);

      const alpha = crystal.opacity;

      // Draw center glow
      const glowRadius = 15;
      const glowGradient = ctx.createRadialGradient(0, 0, 0, 0, 0, glowRadius);
      const glowColor = darkMode
        ? { r: 200, g: 230, b: 255 }
        : { r: 180, g: 220, b: 250 };

      glowGradient.addColorStop(0, `rgba(${glowColor.r}, ${glowColor.g}, ${glowColor.b}, ${alpha * 0.6 * opacityMult})`);
      glowGradient.addColorStop(0.5, `rgba(${glowColor.r}, ${glowColor.g}, ${glowColor.b}, ${alpha * 0.2 * opacityMult})`);
      glowGradient.addColorStop(1, 'rgba(200, 230, 255, 0)');

      ctx.fillStyle = glowGradient;
      ctx.beginPath();
      ctx.arc(0, 0, glowRadius, 0, Math.PI * 2);
      ctx.fill();

      // Draw main branches
      crystal.branches.forEach((branch) => {
        const result = drawCrystalBranch(
          ctx,
          0,
          0,
          branch.angle,
          branch.length,
          2,
          alpha,
          opacityMult
        );

        if (result && branch.length > 10) {
          // Draw sub-branches
          branch.subBranches.forEach((sub) => {
            const subStartX = Math.cos(branch.angle) * branch.length * sub.startRatio;
            const subStartY = Math.sin(branch.angle) * branch.length * sub.startRatio;

            drawCrystalBranch(
              ctx,
              subStartX,
              subStartY,
              branch.angle + sub.angle,
              sub.length,
              1.5,
              alpha * 0.8,
              opacityMult
            );

            // Tiny tertiary branches
            if (sub.length > 10) {
              const tinyAngle = branch.angle + sub.angle + (sub.angle > 0 ? Math.PI / 4 : -Math.PI / 4);
              const tinyStartX = subStartX + Math.cos(branch.angle + sub.angle) * sub.length * 0.6;
              const tinyStartY = subStartY + Math.sin(branch.angle + sub.angle) * sub.length * 0.6;

              drawCrystalBranch(
                ctx,
                tinyStartX,
                tinyStartY,
                tinyAngle,
                sub.length * 0.4,
                1,
                alpha * 0.5,
                opacityMult
              );
            }
          });
        }
      });

      // Center point
      ctx.fillStyle = `rgba(255, 255, 255, ${alpha * opacityMult})`;
      ctx.beginPath();
      ctx.arc(0, 0, 2, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
    };

    const drawFrostParticle = (particle: FrostParticle, opacityMult: number, time: number) => {
      const twinkle = Math.sin(time * 0.005 + particle.phase) * 0.3 + 0.7;
      const alpha = particle.opacity * twinkle * opacityMult;

      const particleColor = darkMode
        ? `rgba(200, 230, 255, ${alpha})`
        : `rgba(180, 220, 250, ${alpha})`;

      ctx.fillStyle = particleColor;
      ctx.beginPath();
      ctx.arc(particle.x, particle.y, particle.size * twinkle, 0, Math.PI * 2);
      ctx.fill();
    };

    const drawFrostEdge = (opacityMult: number, time: number) => {
      // Frost creeping from edges
      const edgeWidth = 80;
      const waveAmplitude = 20;

      // Top edge
      const topGradient = ctx.createLinearGradient(0, 0, 0, edgeWidth);
      topGradient.addColorStop(0, `rgba(200, 230, 255, ${0.15 * opacityMult})`);
      topGradient.addColorStop(1, 'rgba(200, 230, 255, 0)');

      ctx.fillStyle = topGradient;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      for (let x = 0; x <= canvas.width; x += 20) {
        const y = edgeWidth + Math.sin(x * 0.02 + time * 0.001) * waveAmplitude;
        ctx.lineTo(x, y);
      }
      ctx.lineTo(canvas.width, 0);
      ctx.closePath();
      ctx.fill();

      // Bottom edge
      const bottomGradient = ctx.createLinearGradient(0, canvas.height, 0, canvas.height - edgeWidth);
      bottomGradient.addColorStop(0, `rgba(200, 230, 255, ${0.15 * opacityMult})`);
      bottomGradient.addColorStop(1, 'rgba(200, 230, 255, 0)');

      ctx.fillStyle = bottomGradient;
      ctx.beginPath();
      ctx.moveTo(0, canvas.height);
      for (let x = 0; x <= canvas.width; x += 20) {
        const y = canvas.height - edgeWidth - Math.sin(x * 0.02 + time * 0.001 + Math.PI) * waveAmplitude;
        ctx.lineTo(x, y);
      }
      ctx.lineTo(canvas.width, canvas.height);
      ctx.closePath();
      ctx.fill();

      // Left edge
      const leftGradient = ctx.createLinearGradient(0, 0, edgeWidth, 0);
      leftGradient.addColorStop(0, `rgba(200, 230, 255, ${0.1 * opacityMult})`);
      leftGradient.addColorStop(1, 'rgba(200, 230, 255, 0)');

      ctx.fillStyle = leftGradient;
      ctx.fillRect(0, 0, edgeWidth, canvas.height);

      // Right edge
      const rightGradient = ctx.createLinearGradient(canvas.width, 0, canvas.width - edgeWidth, 0);
      rightGradient.addColorStop(0, `rgba(200, 230, 255, ${0.1 * opacityMult})`);
      rightGradient.addColorStop(1, 'rgba(200, 230, 255, 0)');

      ctx.fillStyle = rightGradient;
      ctx.fillRect(canvas.width - edgeWidth, 0, edgeWidth, canvas.height);
    };

    const animate = () => {
      timeRef.current += 16;
      const time = timeRef.current;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const opacityMult = opacity / 50;

      // Draw frost edges
      drawFrostEdge(opacityMult, time);

      // Draw frost particles
      particlesRef.current.forEach((particle) => {
        particle.x += particle.drift + Math.sin(time * 0.002 + particle.phase) * 0.1;
        particle.y += Math.sin(time * 0.001 + particle.phase) * 0.05;

        // Wrap around
        if (particle.x < 0) particle.x = canvas.width;
        if (particle.x > canvas.width) particle.x = 0;
        if (particle.y < 0) particle.y = canvas.height;
        if (particle.y > canvas.height) particle.y = 0;

        drawFrostParticle(particle, opacityMult, time);
      });

      // Spawn new crystals periodically
      if (crystalsRef.current.length < 8 && Math.random() < 0.01) {
        crystalsRef.current.push(createCrystal());
      }

      // Update and draw crystals
      crystalsRef.current = crystalsRef.current.filter((crystal) => {
        if (!crystal.fading) {
          // Fade in
          crystal.opacity = Math.min(1, crystal.opacity + 0.02);

          // Grow branches
          crystal.growth += 1;

          crystal.branches.forEach((branch) => {
            if (branch.length < branch.maxLength) {
              branch.length += branch.maxLength / crystal.maxGrowth;
            } else {
              branch.grown = true;

              // Grow sub-branches
              branch.subBranches.forEach((sub) => {
                if (sub.length < sub.maxLength) {
                  sub.length += sub.maxLength / (crystal.maxGrowth * 0.5);
                }
              });
            }
          });

          // Check if fully grown
          if (crystal.growth >= crystal.maxGrowth + 100) {
            crystal.fading = true;
          }
        } else {
          // Fade out
          crystal.opacity -= 0.008;
          if (crystal.opacity <= 0) {
            return false;
          }
        }

        drawCrystal(crystal, opacityMult);
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
