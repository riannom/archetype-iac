/**
 * Bamboo Sway Animation (Enhanced Forest Version)
 * Dense bamboo forest with many stalks, branches, and leaves swaying in the breeze
 */

import { useRef, useEffect } from 'react';

interface BambooLeaf {
  segment: number;
  side: number;
  size: number;
  angle: number;
  offsetY: number;
  curvature: number;
  colorVariant: number;
}

interface BambooBranch {
  segment: number;
  side: number;
  length: number;
  angle: number;
  leaves: BambooLeaf[];
}

interface BambooStalk {
  x: number;
  baseY: number;
  segments: number;
  segmentHeight: number;
  thickness: number;
  swayPhase: number;
  swaySpeed: number;
  leaves: BambooLeaf[];
  branches: BambooBranch[];
  opacity: number;
  colorScheme: number;
  layer: number; // 0 = back, 1 = mid, 2 = front for depth
}

export function useBambooSway(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const stalksRef = useRef<BambooStalk[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Rich green color schemes
    const colorSchemes = darkMode
      ? [
          { stalk: [60, 80, 55], leaf: [75, 100, 65], accent: [90, 115, 75] },
          { stalk: [50, 70, 45], leaf: [70, 95, 60], accent: [85, 110, 70] },
          { stalk: [55, 75, 50], leaf: [65, 90, 55], accent: [80, 105, 65] },
          { stalk: [45, 65, 40], leaf: [60, 85, 50], accent: [75, 100, 60] },
        ]
      : [
          { stalk: [145, 170, 135], leaf: [160, 190, 150], accent: [175, 205, 165] },
          { stalk: [135, 160, 125], leaf: [155, 185, 145], accent: [170, 200, 160] },
          { stalk: [140, 165, 130], leaf: [150, 180, 140], accent: [165, 195, 155] },
          { stalk: [130, 155, 120], leaf: [145, 175, 135], accent: [160, 190, 150] },
        ];

    const createLeaf = (segment: number, side: number): BambooLeaf => ({
      segment,
      side,
      size: 12 + Math.random() * 22,
      angle: (Math.random() - 0.5) * 0.7,
      offsetY: Math.random() * 0.5,
      curvature: 0.08 + Math.random() * 0.18,
      colorVariant: Math.floor(Math.random() * 4),
    });

    const createBranch = (segment: number, side: number): BambooBranch => {
      const leafCount = 3 + Math.floor(Math.random() * 5); // More leaves per branch
      const leaves: BambooLeaf[] = [];
      for (let i = 0; i < leafCount; i++) {
        leaves.push({
          segment,
          side,
          size: 10 + Math.random() * 18,
          angle: (Math.random() - 0.5) * 0.9 + (i * 0.15 - 0.3),
          offsetY: 0.2 + (i / leafCount) * 0.7,
          curvature: 0.08 + Math.random() * 0.12,
          colorVariant: Math.floor(Math.random() * 4),
        });
      }
      return {
        segment,
        side,
        length: 25 + Math.random() * 50,
        angle: side * (0.25 + Math.random() * 0.5),
        leaves,
      };
    };

    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;

      // Create dense bamboo forest with multiple layers
      const stalks: BambooStalk[] = [];

      // Back layer (smaller, more transparent, more stalks)
      const backCount = Math.floor(canvas.width / 60) + 5;
      for (let i = 0; i < backCount; i++) {
        const segments = 4 + Math.floor(Math.random() * 3);
        const leaves: BambooLeaf[] = [];
        const branches: BambooBranch[] = [];

        for (let s = 1; s < segments; s++) {
          // Many leaves on back stalks
          if (Math.random() < 0.7) {
            const side = Math.random() < 0.5 ? -1 : 1;
            leaves.push(createLeaf(s, side));
            if (Math.random() < 0.5) leaves.push(createLeaf(s, side));
          }
          if (Math.random() < 0.8) {
            const side = Math.random() < 0.5 ? -1 : 1;
            branches.push(createBranch(s, side));
            if (Math.random() < 0.4) branches.push(createBranch(s, -side));
          }
        }

        stalks.push({
          x: (canvas.width / backCount) * i + Math.random() * 50 - 25,
          baseY: canvas.height + 30,
          segments,
          segmentHeight: 40 + Math.random() * 25,
          thickness: 5 + Math.random() * 3,
          swayPhase: Math.random() * Math.PI * 2,
          swaySpeed: 0.15 + Math.random() * 0.2,
          leaves,
          branches,
          opacity: 0.06 + Math.random() * 0.04,
          colorScheme: Math.floor(Math.random() * 4),
          layer: 0,
        });
      }

      // Mid layer
      const midCount = Math.floor(canvas.width / 100) + 4;
      for (let i = 0; i < midCount; i++) {
        const segments = 5 + Math.floor(Math.random() * 4);
        const leaves: BambooLeaf[] = [];
        const branches: BambooBranch[] = [];

        for (let s = 2; s < segments; s++) {
          if (Math.random() < 0.6) {
            const side = Math.random() < 0.5 ? -1 : 1;
            leaves.push(createLeaf(s, side));
            if (Math.random() < 0.4) leaves.push(createLeaf(s, -side));
          }
          if (Math.random() < 0.75) {
            const side = Math.random() < 0.5 ? -1 : 1;
            branches.push(createBranch(s, side));
            if (Math.random() < 0.5) branches.push(createBranch(s, -side));
          }
        }

        stalks.push({
          x: (canvas.width / midCount) * i + Math.random() * 80 - 40,
          baseY: canvas.height + 25,
          segments,
          segmentHeight: 50 + Math.random() * 35,
          thickness: 7 + Math.random() * 4,
          swayPhase: Math.random() * Math.PI * 2,
          swaySpeed: 0.2 + Math.random() * 0.25,
          leaves,
          branches,
          opacity: 0.09 + Math.random() * 0.05,
          colorScheme: Math.floor(Math.random() * 4),
          layer: 1,
        });
      }

      // Front layer (largest, most visible)
      const frontCount = Math.floor(canvas.width / 150) + 3;
      for (let i = 0; i < frontCount; i++) {
        const segments = 6 + Math.floor(Math.random() * 4);
        const leaves: BambooLeaf[] = [];
        const branches: BambooBranch[] = [];

        for (let s = 2; s < segments; s++) {
          if (Math.random() < 0.5) {
            const side = Math.random() < 0.5 ? -1 : 1;
            leaves.push(createLeaf(s, side));
            if (Math.random() < 0.35) leaves.push(createLeaf(s, side));
          }
          if (Math.random() < 0.7) {
            const side = Math.random() < 0.5 ? -1 : 1;
            branches.push(createBranch(s, side));
            if (Math.random() < 0.45) branches.push(createBranch(s, -side));
          }
        }

        stalks.push({
          x: (canvas.width / frontCount) * i + Math.random() * 100 - 50,
          baseY: canvas.height + 20,
          segments,
          segmentHeight: 55 + Math.random() * 40,
          thickness: 9 + Math.random() * 5,
          swayPhase: Math.random() * Math.PI * 2,
          swaySpeed: 0.25 + Math.random() * 0.35,
          leaves,
          branches,
          opacity: 0.12 + Math.random() * 0.06,
          colorScheme: Math.floor(Math.random() * 4),
          layer: 2,
        });
      }

      // Sort by layer for proper depth rendering
      stalksRef.current = stalks.sort((a, b) => a.layer - b.layer);
    };

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const drawLeaf = (
      x: number,
      y: number,
      leaf: BambooLeaf,
      sway: number,
      colors: typeof colorSchemes[0],
      baseOpacity: number,
      opacityMult: number
    ) => {
      const leafSway = sway * 0.3;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(leaf.side * (0.65 + leafSway) + leaf.angle);

      const colorBase = leaf.colorVariant < 2 ? colors.leaf : colors.accent;
      const leafOpacity = baseOpacity * opacityMult * (0.55 + leaf.colorVariant * 0.08);

      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.bezierCurveTo(
        leaf.size * 0.22, -leaf.size * (0.07 + leaf.curvature),
        leaf.size * 0.58, -leaf.size * 0.1,
        leaf.size, 0
      );
      ctx.bezierCurveTo(
        leaf.size * 0.58, leaf.size * 0.08,
        leaf.size * 0.22, leaf.size * (0.05 + leaf.curvature * 0.5),
        0, 0
      );

      ctx.fillStyle = `rgba(${colorBase[0]}, ${colorBase[1]}, ${colorBase[2]}, ${leafOpacity})`;
      ctx.fill();

      // Subtle vein
      ctx.beginPath();
      ctx.moveTo(2, 0);
      ctx.quadraticCurveTo(leaf.size * 0.5, -leaf.size * 0.015, leaf.size * 0.85, 0);
      ctx.strokeStyle = `rgba(${colorBase[0] - 15}, ${colorBase[1] - 8}, ${colorBase[2] - 15}, ${leafOpacity * 0.35})`;
      ctx.lineWidth = 0.4;
      ctx.stroke();

      ctx.restore();
    };

    const drawBamboo = (stalk: BambooStalk) => {
      const opacityMultiplier = opacity / 50;
      const colors = colorSchemes[stalk.colorScheme];
      const stalkColor = `rgba(${colors.stalk[0]}, ${colors.stalk[1]}, ${colors.stalk[2]}, ${stalk.opacity * opacityMultiplier})`;

      const sway = Math.sin(timeRef.current * stalk.swaySpeed + stalk.swayPhase) * 1.8;
      const secondarySway = Math.sin(timeRef.current * stalk.swaySpeed * 1.4 + stalk.swayPhase + 1) * 1.5;

      let prevX = stalk.x;
      let prevY = stalk.baseY;

      for (let i = 0; i < stalk.segments; i++) {
        const swayOffset = sway * (i + 1) * 2.5;
        const segY = stalk.baseY - (i + 1) * stalk.segmentHeight;
        const segX = stalk.x + swayOffset;

        const segmentWidth = stalk.thickness * (1 - i * 0.06);
        ctx.beginPath();
        ctx.moveTo(prevX, prevY);
        ctx.lineTo(segX, segY);
        ctx.strokeStyle = stalkColor;
        ctx.lineWidth = segmentWidth;
        ctx.lineCap = 'round';
        ctx.stroke();

        // Highlight
        ctx.beginPath();
        ctx.moveTo(prevX + segmentWidth * 0.2, prevY);
        ctx.lineTo(segX + segmentWidth * 0.2, segY);
        ctx.strokeStyle = `rgba(${colors.accent[0]}, ${colors.accent[1]}, ${colors.accent[2]}, ${stalk.opacity * opacityMultiplier * 0.25})`;
        ctx.lineWidth = segmentWidth * 0.25;
        ctx.stroke();

        // Node
        ctx.beginPath();
        ctx.ellipse(segX, segY, stalk.thickness * 0.85, 3.5, 0, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${colors.stalk[0] - 12}, ${colors.stalk[1] - 8}, ${colors.stalk[2] - 12}, ${stalk.opacity * opacityMultiplier * 1.15})`;
        ctx.fill();

        // Draw branches
        stalk.branches.forEach((branch) => {
          if (branch.segment === i) {
            const branchSway = sway * 0.45 + secondarySway * 0.25;
            ctx.save();
            ctx.translate(segX, segY);
            ctx.rotate(branch.angle + branchSway * 0.35);

            ctx.beginPath();
            ctx.moveTo(0, 0);
            ctx.quadraticCurveTo(branch.length * 0.5, -branch.length * 0.08, branch.length, 0);
            ctx.strokeStyle = `rgba(${colors.stalk[0]}, ${colors.stalk[1]}, ${colors.stalk[2]}, ${stalk.opacity * opacityMultiplier * 0.65})`;
            ctx.lineWidth = 1.8;
            ctx.stroke();

            branch.leaves.forEach((leaf) => {
              const leafX = branch.length * leaf.offsetY;
              const leafY = -branch.length * 0.08 * leaf.offsetY * (1 - leaf.offsetY) * 4;
              drawLeaf(leafX, leafY, leaf, branchSway, colors, stalk.opacity, opacityMultiplier);
            });

            ctx.restore();
          }
        });

        // Direct leaves
        stalk.leaves.forEach((leaf) => {
          if (leaf.segment === i) {
            drawLeaf(segX, segY, leaf, sway, colors, stalk.opacity, opacityMultiplier);
          }
        });

        prevX = segX;
        prevY = segY;
      }
    };

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      stalksRef.current.forEach(drawBamboo);

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
