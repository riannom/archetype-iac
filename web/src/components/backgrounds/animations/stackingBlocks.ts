/**
 * Stacking Blocks Animation
 * Bitcoin blocks gently falling and stacking
 */

import { useRef, useEffect, useCallback } from 'react';

interface Block {
  x: number;
  y: number;
  targetY: number;
  size: number;
  rotation: number;
  opacity: number;
  settled: boolean;
  settleTime: number;
  column: number;
  hueShift: number; // Color variation
}

export function useStackingBlocks(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const blocksRef = useRef<Block[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);
  const columnHeightsRef = useRef<number[]>([]);

  const createBlock = useCallback((canvas: HTMLCanvasElement, column: number, columnHeights: number[], time: number): Block => {
    const size = 25 + Math.random() * 15;
    const targetY = canvas.height - columnHeights[column] - size / 2;

    return {
      x: (column + 0.5) * (canvas.width / columnHeights.length),
      y: -size,
      targetY,
      size,
      rotation: (Math.random() - 0.5) * 0.1,
      opacity: 0.2 + Math.random() * 0.2,
      settled: false,
      settleTime: 0,
      column,
      hueShift: (time * 0.5 + Math.random() * 20) % 40 - 20, // Subtle color variation over time
    };
  }, []);

  const drawBlock = useCallback((
    ctx: CanvasRenderingContext2D,
    block: Block,
    isDark: boolean,
    opacityMultiplier: number,
    time: number
  ) => {
    ctx.save();
    ctx.translate(block.x, block.y);
    ctx.rotate(block.rotation);

    const s = block.size;
    let alpha = block.opacity * opacityMultiplier;

    // 10 minute duration (600 seconds) - fade starts at 570s, complete by 600s
    if (block.settled) {
      const fadeTime = time - block.settleTime;
      if (fadeTime > 570) {
        alpha *= Math.max(0, 1 - (fadeTime - 570) / 30);
      }
    }

    // Apply hue shift for color variation
    const hueOffset = block.hueShift;
    const baseColor = isDark
      ? { r: 180 + hueOffset, g: 140 + hueOffset * 0.5, b: 40 }
      : { r: 160 + hueOffset, g: 120 + hueOffset * 0.5, b: 30 };

    // Top face
    ctx.fillStyle = `rgba(${baseColor.r + 40}, ${baseColor.g + 30}, ${baseColor.b + 20}, ${alpha})`;
    ctx.beginPath();
    ctx.moveTo(-s / 2, -s / 4);
    ctx.lineTo(0, -s / 2);
    ctx.lineTo(s / 2, -s / 4);
    ctx.lineTo(0, 0);
    ctx.closePath();
    ctx.fill();

    // Left face
    ctx.fillStyle = `rgba(${baseColor.r}, ${baseColor.g}, ${baseColor.b}, ${alpha})`;
    ctx.beginPath();
    ctx.moveTo(-s / 2, -s / 4);
    ctx.lineTo(0, 0);
    ctx.lineTo(0, s / 2);
    ctx.lineTo(-s / 2, s / 4);
    ctx.closePath();
    ctx.fill();

    // Right face
    ctx.fillStyle = `rgba(${baseColor.r - 30}, ${baseColor.g - 20}, ${baseColor.b}, ${alpha})`;
    ctx.beginPath();
    ctx.moveTo(s / 2, -s / 4);
    ctx.lineTo(0, 0);
    ctx.lineTo(0, s / 2);
    ctx.lineTo(s / 2, s / 4);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = `rgba(255, 255, 255, ${alpha * 0.3})`;
    ctx.font = `bold ${s * 0.25}px Arial`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('₿', 0, -s / 5);

    ctx.restore();
  }, []);

  useEffect(() => {
    if (!active) return;

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

    const columnCount = Math.floor(canvas.width / 80);
    columnHeightsRef.current = Array(columnCount).fill(0);

    blocksRef.current = [];
    for (let i = 0; i < 3; i++) {
      const col = Math.floor(Math.random() * columnCount);
      const block = createBlock(canvas, col, columnHeightsRef.current, 0);
      block.y = block.targetY;
      block.settled = true;
      block.settleTime = 0;
      columnHeightsRef.current[col] += block.size;
      blocksRef.current.push(block);
    }

    let lastBlockTime = 0;

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      if (timeRef.current - lastBlockTime > 2 + Math.random() * 3) {
        const col = Math.floor(Math.random() * columnHeightsRef.current.length);
        if (columnHeightsRef.current[col] < canvas.height * 0.6) {
          blocksRef.current.push(createBlock(canvas, col, columnHeightsRef.current, timeRef.current));
          lastBlockTime = timeRef.current;
        }
      }

      blocksRef.current = blocksRef.current.filter((block) => {
        if (!block.settled) {
          block.y += 2;
          if (block.y >= block.targetY) {
            block.y = block.targetY;
            block.settled = true;
            block.settleTime = timeRef.current;
            columnHeightsRef.current[block.column] += block.size;
          }
        }

        // 10 minute duration (600 seconds)
        if (block.settled) {
          const fadeTime = timeRef.current - block.settleTime;
          if (fadeTime > 600) {
            columnHeightsRef.current[block.column] = Math.max(
              0,
              columnHeightsRef.current[block.column] - block.size
            );
            return false;
          }
        }

        drawBlock(ctx, block, darkMode, opacityMultiplier, timeRef.current);
        return true;
      });

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [canvasRef, darkMode, opacity, createBlock, drawBlock, active]);
}
