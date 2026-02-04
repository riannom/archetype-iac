/**
 * Lotus Bloom Animation
 * Graceful lotus flowers with opening/closing petals
 */

import { useRef, useEffect } from 'react';

interface LotusFlower {
  x: number;
  y: number;
  size: number;
  rotation: number;
  bloomPhase: number;
  bloomSpeed: number;
  petalCount: number;
  colorScheme: number;
  opacity: number;
  bobPhase: number;
  bobSpeed: number;
}

export function useLotusBloom(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const flowersRef = useRef<LotusFlower[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Soft lotus colors
    const colorSchemes = darkMode
      ? [
          { inner: [255, 200, 210], outer: [255, 180, 195], center: [255, 220, 150] },
          { inner: [255, 245, 240], outer: [255, 230, 235], center: [255, 210, 140] },
          { inner: [255, 210, 220], outer: [255, 190, 205], center: [255, 215, 145] },
        ]
      : [
          { inner: [255, 180, 195], outer: [255, 150, 170], center: [255, 200, 120] },
          { inner: [255, 225, 220], outer: [255, 200, 210], center: [255, 190, 110] },
          { inner: [255, 190, 205], outer: [255, 160, 180], center: [255, 195, 115] },
        ];

    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;

      const flowerCount = Math.floor((canvas.width * canvas.height) / 150000) + 3;
      flowersRef.current = Array.from({ length: flowerCount }, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        size: 30 + Math.random() * 40,
        rotation: Math.random() * Math.PI * 2,
        bloomPhase: Math.random(),
        bloomSpeed: 0.1 + Math.random() * 0.15,
        petalCount: 8 + Math.floor(Math.random() * 4),
        colorScheme: Math.floor(Math.random() * 3),
        opacity: 0.08 + Math.random() * 0.08,
        bobPhase: Math.random() * Math.PI * 2,
        bobSpeed: 0.3 + Math.random() * 0.2,
      }));
    };
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const drawLotus = (flower: LotusFlower) => {
      const opacityMultiplier = opacity / 50;
      const colors = colorSchemes[flower.colorScheme];
      const bloom = (Math.sin(timeRef.current * flower.bloomSpeed + flower.bloomPhase * Math.PI * 2) + 1) / 2;
      const bobY = Math.sin(timeRef.current * flower.bobSpeed + flower.bobPhase) * 3;

      ctx.save();
      ctx.translate(flower.x, flower.y + bobY);
      ctx.rotate(flower.rotation);

      // Draw petals (outer layer)
      for (let i = 0; i < flower.petalCount; i++) {
        const angle = (i / flower.petalCount) * Math.PI * 2;
        const petalOpen = 0.3 + bloom * 0.7;

        ctx.save();
        ctx.rotate(angle);

        // Outer petal
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.bezierCurveTo(
          flower.size * 0.2 * petalOpen, -flower.size * 0.3,
          flower.size * 0.4 * petalOpen, -flower.size * 0.8,
          0, -flower.size * petalOpen
        );
        ctx.bezierCurveTo(
          -flower.size * 0.4 * petalOpen, -flower.size * 0.8,
          -flower.size * 0.2 * petalOpen, -flower.size * 0.3,
          0, 0
        );
        ctx.fillStyle = `rgba(${colors.outer[0]}, ${colors.outer[1]}, ${colors.outer[2]}, ${flower.opacity * opacityMultiplier * 0.6})`;
        ctx.fill();

        ctx.restore();
      }

      // Draw petals (inner layer)
      for (let i = 0; i < flower.petalCount; i++) {
        const angle = (i / flower.petalCount) * Math.PI * 2 + Math.PI / flower.petalCount;
        const petalOpen = 0.4 + bloom * 0.6;

        ctx.save();
        ctx.rotate(angle);

        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.bezierCurveTo(
          flower.size * 0.15 * petalOpen, -flower.size * 0.2,
          flower.size * 0.3 * petalOpen, -flower.size * 0.5,
          0, -flower.size * 0.7 * petalOpen
        );
        ctx.bezierCurveTo(
          -flower.size * 0.3 * petalOpen, -flower.size * 0.5,
          -flower.size * 0.15 * petalOpen, -flower.size * 0.2,
          0, 0
        );
        ctx.fillStyle = `rgba(${colors.inner[0]}, ${colors.inner[1]}, ${colors.inner[2]}, ${flower.opacity * opacityMultiplier * 0.7})`;
        ctx.fill();

        ctx.restore();
      }

      // Draw center
      ctx.beginPath();
      ctx.arc(0, 0, flower.size * 0.15, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${colors.center[0]}, ${colors.center[1]}, ${colors.center[2]}, ${flower.opacity * opacityMultiplier})`;
      ctx.fill();

      ctx.restore();
    };

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      flowersRef.current.forEach(drawLotus);

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [canvasRef, darkMode, opacity, active]);
}
