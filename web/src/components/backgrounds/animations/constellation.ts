/**
 * Constellation Animation
 * Connected nodes representing the Bitcoin network
 */

import { useRef, useEffect, useCallback } from 'react';

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  connections: number[];
  pulsePhase: number;
}

export function useConstellation(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const nodesRef = useRef<Node[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  const createNode = useCallback((canvas: HTMLCanvasElement): Node => {
    return {
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      size: 2 + Math.random() * 3,
      connections: [],
      pulsePhase: Math.random() * Math.PI * 2,
    };
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

    const nodeCount = Math.floor((canvas.width * canvas.height) / 30000);
    nodesRef.current = Array.from({ length: Math.max(20, nodeCount) }, () =>
      createNode(canvas)
    );

    const connectionDistance = 150;

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;
      const nodes = nodesRef.current;

      nodes.forEach((node) => {
        node.x += node.vx;
        node.y += node.vy;

        if (node.x < 0 || node.x > canvas.width) node.vx *= -1;
        if (node.y < 0 || node.y > canvas.height) node.vy *= -1;

        node.x = Math.max(0, Math.min(canvas.width, node.x));
        node.y = Math.max(0, Math.min(canvas.height, node.y));
      });

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const distance = Math.sqrt(dx * dx + dy * dy);

          if (distance < connectionDistance) {
            const lineOpacity = (1 - distance / connectionDistance) * 0.3 * opacityMultiplier;

            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.strokeStyle = darkMode
              ? `rgba(150, 180, 255, ${lineOpacity})`
              : `rgba(70, 100, 180, ${lineOpacity})`;
            ctx.lineWidth = 1;
            ctx.stroke();
          }
        }
      }

      nodes.forEach((node) => {
        const pulse = 1 + Math.sin(timeRef.current * 2 + node.pulsePhase) * 0.3;
        const size = node.size * pulse;

        const gradient = ctx.createRadialGradient(
          node.x, node.y, 0,
          node.x, node.y, size * 3
        );

        if (darkMode) {
          gradient.addColorStop(0, `rgba(150, 180, 255, ${0.4 * opacityMultiplier})`);
          gradient.addColorStop(1, 'rgba(150, 180, 255, 0)');
        } else {
          gradient.addColorStop(0, `rgba(70, 100, 180, ${0.3 * opacityMultiplier})`);
          gradient.addColorStop(1, 'rgba(70, 100, 180, 0)');
        }

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(node.x, node.y, size * 3, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = darkMode
          ? `rgba(200, 220, 255, ${0.8 * opacityMultiplier})`
          : `rgba(50, 80, 150, ${0.8 * opacityMultiplier})`;
        ctx.beginPath();
        ctx.arc(node.x, node.y, size, 0, Math.PI * 2);
        ctx.fill();
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
  }, [canvasRef, darkMode, opacity, createNode, active]);
}
