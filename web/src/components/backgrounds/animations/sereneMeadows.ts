/**
 * Serene Meadows Animation
 * Soft rolling hills with gentle mist, trees, flowers, and peaceful scenery
 */

import { useRef, useEffect } from 'react';

interface Hill {
  baseY: number;
  amplitude: number;
  frequency: number;
  phase: number;
  colorIndex: number;
  trees: TreeSilhouette[];
}

interface TreeSilhouette {
  x: number;
  size: number;
  type: 'round' | 'pine' | 'willow';
  sway: number;
  swaySpeed: number;
}

interface MistLayer {
  y: number;
  speed: number;
  opacity: number;
  waveAmplitude: number;
  waveFrequency: number;
  phase: number;
}

interface Flower {
  x: number;
  y: number;
  size: number;
  colorIndex: number;
  swayPhase: number;
  type: 'daisy' | 'tulip' | 'lavender';
}

interface Bird {
  x: number;
  y: number;
  size: number;
  speed: number;
  wingPhase: number;
  wingSpeed: number;
}

interface Cloud {
  x: number;
  y: number;
  width: number;
  speed: number;
  opacity: number;
}

export function useSereneMeadows(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const hillsRef = useRef<Hill[]>([]);
  const mistRef = useRef<MistLayer[]>([]);
  const flowersRef = useRef<Flower[]>([]);
  const birdsRef = useRef<Bird[]>([]);
  const cloudsRef = useRef<Cloud[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Soft, serene color palettes
    const hillColors = darkMode
      ? [
          [45, 55, 75],    // Deep blue-gray (far)
          [55, 70, 85],    // Muted blue
          [50, 75, 70],    // Soft teal
          [60, 80, 65],    // Muted sage
          [55, 70, 55],    // Soft forest (near)
        ]
      : [
          [180, 160, 200], // Soft lavender (far)
          [160, 180, 190], // Pale blue
          [170, 195, 175], // Soft sage
          [145, 175, 140], // Light green
          [120, 155, 110], // Meadow green (near)
        ];

    const mistColors = darkMode
      ? [[140, 150, 180], [130, 145, 170], [150, 160, 185]]
      : [[220, 210, 235], [210, 220, 240], [230, 225, 245]];

    const flowerColors = darkMode
      ? [
          [255, 200, 220], // Soft pink
          [220, 180, 255], // Lavender
          [255, 230, 180], // Cream
          [200, 220, 255], // Pale blue
          [255, 210, 200], // Peach
        ]
      : [
          [255, 180, 200], // Pink
          [200, 160, 235], // Purple
          [255, 220, 150], // Yellow
          [180, 200, 255], // Blue
          [255, 190, 180], // Coral
        ];

    const skyGradient = darkMode
      ? { top: [25, 30, 50], bottom: [50, 55, 80] }
      : { top: [180, 200, 230], bottom: [240, 220, 210] };

    const createTree = (x: number, hillY: number, layerIndex: number): TreeSilhouette => {
      const types: TreeSilhouette['type'][] = ['round', 'pine', 'willow'];
      return {
        x,
        size: (15 + Math.random() * 25) * (1 - layerIndex * 0.15),
        type: types[Math.floor(Math.random() * types.length)],
        sway: Math.random() * Math.PI * 2,
        swaySpeed: 0.3 + Math.random() * 0.3,
      };
    };

    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;

      // Create rolling hills with soft curves
      hillsRef.current = [];
      const hillCount = 5;
      for (let i = 0; i < hillCount; i++) {
        const baseY = canvas.height * (0.35 + i * 0.13);
        const trees: TreeSilhouette[] = [];

        // Add trees to hills (more on closer hills)
        const treeCount = Math.floor((3 + i * 2) * (canvas.width / 800));
        for (let t = 0; t < treeCount; t++) {
          if (Math.random() < 0.7) {
            trees.push(createTree(
              Math.random() * canvas.width,
              baseY,
              i
            ));
          }
        }

        hillsRef.current.push({
          baseY,
          amplitude: 30 + Math.random() * 40 + i * 10,
          frequency: 0.001 + Math.random() * 0.001,
          phase: Math.random() * Math.PI * 2,
          colorIndex: i,
          trees,
        });
      }

      // Create soft mist layers
      mistRef.current = Array.from({ length: 4 }, (_, i) => ({
        y: canvas.height * (0.4 + i * 0.12),
        speed: 0.05 + Math.random() * 0.08,
        opacity: 0.08 + Math.random() * 0.06,
        waveAmplitude: 15 + Math.random() * 20,
        waveFrequency: 0.001 + Math.random() * 0.001,
        phase: Math.random() * Math.PI * 2,
      }));

      // Create flowers in foreground
      const flowerCount = Math.floor(canvas.width / 40);
      flowersRef.current = Array.from({ length: flowerCount }, () => {
        const types: Flower['type'][] = ['daisy', 'tulip', 'lavender'];
        return {
          x: Math.random() * canvas.width,
          y: canvas.height * (0.85 + Math.random() * 0.12),
          size: 4 + Math.random() * 6,
          colorIndex: Math.floor(Math.random() * flowerColors.length),
          swayPhase: Math.random() * Math.PI * 2,
          type: types[Math.floor(Math.random() * types.length)],
        };
      });

      // Create distant birds
      birdsRef.current = Array.from({ length: 3 + Math.floor(Math.random() * 3) }, () => ({
        x: Math.random() * canvas.width,
        y: canvas.height * (0.1 + Math.random() * 0.2),
        size: 3 + Math.random() * 4,
        speed: 0.2 + Math.random() * 0.3,
        wingPhase: Math.random() * Math.PI * 2,
        wingSpeed: 3 + Math.random() * 2,
      }));

      // Create soft clouds
      cloudsRef.current = Array.from({ length: 4 }, () => ({
        x: Math.random() * canvas.width,
        y: canvas.height * (0.08 + Math.random() * 0.15),
        width: 80 + Math.random() * 120,
        speed: 0.05 + Math.random() * 0.1,
        opacity: 0.12 + Math.random() * 0.08,
      }));
    };

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const getHillY = (hill: Hill, x: number): number => {
      // Use multiple sine waves for smooth, natural rolling hills
      const wave1 = Math.sin(x * hill.frequency + hill.phase) * hill.amplitude;
      const wave2 = Math.sin(x * hill.frequency * 2.3 + hill.phase * 1.5) * hill.amplitude * 0.3;
      const wave3 = Math.sin(x * hill.frequency * 0.5 + hill.phase * 0.7) * hill.amplitude * 0.5;
      return hill.baseY + wave1 + wave2 + wave3;
    };

    const drawSky = (opacityMult: number) => {
      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height * 0.6);
      gradient.addColorStop(0, `rgba(${skyGradient.top[0]}, ${skyGradient.top[1]}, ${skyGradient.top[2]}, ${0.3 * opacityMult})`);
      gradient.addColorStop(1, `rgba(${skyGradient.bottom[0]}, ${skyGradient.bottom[1]}, ${skyGradient.bottom[2]}, ${0.2 * opacityMult})`);
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height * 0.6);

      // Soft sun/moon glow
      const sunX = canvas.width * 0.75;
      const sunY = canvas.height * 0.15;
      const sunGradient = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, 150);
      const glowColor = darkMode ? [200, 190, 160] : [255, 240, 200];
      sunGradient.addColorStop(0, `rgba(${glowColor[0]}, ${glowColor[1]}, ${glowColor[2]}, ${0.2 * opacityMult})`);
      sunGradient.addColorStop(0.5, `rgba(${glowColor[0]}, ${glowColor[1]}, ${glowColor[2]}, ${0.08 * opacityMult})`);
      sunGradient.addColorStop(1, 'rgba(255, 255, 255, 0)');
      ctx.fillStyle = sunGradient;
      ctx.beginPath();
      ctx.arc(sunX, sunY, 150, 0, Math.PI * 2);
      ctx.fill();
    };

    const drawCloud = (cloud: Cloud, opacityMult: number) => {
      const cloudColor = darkMode ? [160, 170, 190] : [255, 255, 255];

      ctx.save();
      ctx.translate(cloud.x, cloud.y);

      // Draw soft puffy cloud with multiple circles
      const puffs = [
        { x: 0, y: 0, r: cloud.width * 0.25 },
        { x: cloud.width * 0.2, y: -cloud.width * 0.05, r: cloud.width * 0.3 },
        { x: cloud.width * 0.45, y: 0, r: cloud.width * 0.25 },
        { x: cloud.width * 0.15, y: cloud.width * 0.08, r: cloud.width * 0.2 },
        { x: cloud.width * 0.35, y: cloud.width * 0.06, r: cloud.width * 0.18 },
      ];

      puffs.forEach(puff => {
        const gradient = ctx.createRadialGradient(puff.x, puff.y, 0, puff.x, puff.y, puff.r);
        gradient.addColorStop(0, `rgba(${cloudColor[0]}, ${cloudColor[1]}, ${cloudColor[2]}, ${cloud.opacity * opacityMult})`);
        gradient.addColorStop(1, `rgba(${cloudColor[0]}, ${cloudColor[1]}, ${cloudColor[2]}, 0)`);
        ctx.beginPath();
        ctx.arc(puff.x, puff.y, puff.r, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
      });

      ctx.restore();
    };

    const drawBird = (bird: Bird, opacityMult: number) => {
      const wingAngle = Math.sin(timeRef.current * bird.wingSpeed + bird.wingPhase) * 0.4;
      const birdColor = darkMode ? [80, 80, 90] : [60, 60, 70];

      ctx.save();
      ctx.translate(bird.x, bird.y);

      // Simple V-shaped bird
      ctx.beginPath();
      ctx.moveTo(-bird.size, Math.sin(wingAngle) * bird.size * 0.5);
      ctx.quadraticCurveTo(0, -bird.size * 0.2, bird.size, Math.sin(wingAngle) * bird.size * 0.5);
      ctx.strokeStyle = `rgba(${birdColor[0]}, ${birdColor[1]}, ${birdColor[2]}, ${0.35 * opacityMult})`;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.restore();
    };

    const drawHill = (hill: Hill, opacityMult: number) => {
      const colors = hillColors[hill.colorIndex];

      ctx.beginPath();
      ctx.moveTo(0, canvas.height);

      // Draw smooth rolling hill curve
      for (let x = 0; x <= canvas.width; x += 3) {
        const y = getHillY(hill, x);
        if (x === 0) {
          ctx.lineTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }

      ctx.lineTo(canvas.width, canvas.height);
      ctx.closePath();

      // Gradient fill for depth
      const gradient = ctx.createLinearGradient(0, hill.baseY - hill.amplitude, 0, canvas.height);
      gradient.addColorStop(0, `rgba(${colors[0]}, ${colors[1]}, ${colors[2]}, ${0.3 * opacityMult})`);
      gradient.addColorStop(0.5, `rgba(${colors[0] - 5}, ${colors[1] - 5}, ${colors[2] - 5}, ${0.25 * opacityMult})`);
      gradient.addColorStop(1, `rgba(${colors[0] - 10}, ${colors[1] - 10}, ${colors[2] - 10}, ${0.2 * opacityMult})`);
      ctx.fillStyle = gradient;
      ctx.fill();

      // Draw trees on this hill
      hill.trees.forEach(tree => {
        const treeY = getHillY(hill, tree.x);
        const sway = Math.sin(timeRef.current * tree.swaySpeed + tree.sway) * 2;
        const treeColor = darkMode
          ? [colors[0] - 15, colors[1] - 10, colors[2] - 15]
          : [colors[0] - 20, colors[1] - 15, colors[2] - 20];

        ctx.save();
        ctx.translate(tree.x + sway, treeY);

        if (tree.type === 'round') {
          // Round deciduous tree
          ctx.beginPath();
          ctx.arc(0, -tree.size * 0.6, tree.size * 0.5, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${treeColor[0]}, ${treeColor[1]}, ${treeColor[2]}, ${0.35 * opacityMult})`;
          ctx.fill();

          // Trunk
          ctx.fillRect(-tree.size * 0.08, -tree.size * 0.2, tree.size * 0.16, tree.size * 0.25);
        } else if (tree.type === 'pine') {
          // Pine tree
          ctx.beginPath();
          ctx.moveTo(0, -tree.size);
          ctx.lineTo(-tree.size * 0.4, 0);
          ctx.lineTo(tree.size * 0.4, 0);
          ctx.closePath();
          ctx.fillStyle = `rgba(${treeColor[0]}, ${treeColor[1]}, ${treeColor[2]}, ${0.35 * opacityMult})`;
          ctx.fill();
        } else {
          // Willow tree
          ctx.beginPath();
          ctx.arc(0, -tree.size * 0.4, tree.size * 0.35, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${treeColor[0]}, ${treeColor[1]}, ${treeColor[2]}, ${0.3 * opacityMult})`;
          ctx.fill();

          // Drooping branches
          for (let b = 0; b < 5; b++) {
            const angle = (b / 5) * Math.PI - Math.PI * 0.5;
            const branchSway = Math.sin(timeRef.current * 0.5 + b) * 3;
            ctx.beginPath();
            ctx.moveTo(Math.cos(angle) * tree.size * 0.3, -tree.size * 0.4 + Math.sin(angle) * tree.size * 0.3);
            ctx.quadraticCurveTo(
              Math.cos(angle) * tree.size * 0.5 + branchSway,
              tree.size * 0.1,
              Math.cos(angle) * tree.size * 0.4 + branchSway,
              tree.size * 0.3
            );
            ctx.strokeStyle = `rgba(${treeColor[0]}, ${treeColor[1]}, ${treeColor[2]}, ${0.25 * opacityMult})`;
            ctx.lineWidth = 2;
            ctx.stroke();
          }
        }

        ctx.restore();
      });
    };

    const drawMist = (mist: MistLayer, opacityMult: number) => {
      const colors = mistColors[Math.floor(Math.random() * mistColors.length)];

      ctx.beginPath();
      ctx.moveTo(0, canvas.height);

      for (let x = 0; x <= canvas.width; x += 5) {
        const wave = Math.sin(x * mist.waveFrequency + timeRef.current * mist.speed + mist.phase) * mist.waveAmplitude;
        const y = mist.y + wave;
        ctx.lineTo(x, y);
      }

      ctx.lineTo(canvas.width, canvas.height);
      ctx.closePath();

      const gradient = ctx.createLinearGradient(0, mist.y - mist.waveAmplitude, 0, mist.y + 80);
      gradient.addColorStop(0, `rgba(${colors[0]}, ${colors[1]}, ${colors[2]}, ${mist.opacity * opacityMult})`);
      gradient.addColorStop(1, `rgba(${colors[0]}, ${colors[1]}, ${colors[2]}, 0)`);
      ctx.fillStyle = gradient;
      ctx.fill();
    };

    const drawFlower = (flower: Flower, opacityMult: number) => {
      const colors = flowerColors[flower.colorIndex];
      const sway = Math.sin(timeRef.current * 1.5 + flower.swayPhase) * 2;

      ctx.save();
      ctx.translate(flower.x + sway, flower.y);

      // Stem
      const stemColor = darkMode ? [60, 80, 50] : [80, 120, 60];
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.quadraticCurveTo(sway * 0.5, -flower.size * 1.5, 0, -flower.size * 3);
      ctx.strokeStyle = `rgba(${stemColor[0]}, ${stemColor[1]}, ${stemColor[2]}, ${0.3 * opacityMult})`;
      ctx.lineWidth = 1;
      ctx.stroke();

      // Flower head
      ctx.translate(0, -flower.size * 3);

      if (flower.type === 'daisy') {
        // Daisy petals
        for (let i = 0; i < 6; i++) {
          ctx.save();
          ctx.rotate((i / 6) * Math.PI * 2);
          ctx.beginPath();
          ctx.ellipse(0, -flower.size * 0.6, flower.size * 0.25, flower.size * 0.5, 0, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${colors[0]}, ${colors[1]}, ${colors[2]}, ${0.35 * opacityMult})`;
          ctx.fill();
          ctx.restore();
        }
        // Center
        ctx.beginPath();
        ctx.arc(0, 0, flower.size * 0.3, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 220, 100, ${0.4 * opacityMult})`;
        ctx.fill();
      } else if (flower.type === 'tulip') {
        // Tulip shape
        ctx.beginPath();
        ctx.moveTo(0, -flower.size);
        ctx.bezierCurveTo(-flower.size * 0.5, -flower.size * 0.5, -flower.size * 0.4, 0, 0, flower.size * 0.2);
        ctx.bezierCurveTo(flower.size * 0.4, 0, flower.size * 0.5, -flower.size * 0.5, 0, -flower.size);
        ctx.fillStyle = `rgba(${colors[0]}, ${colors[1]}, ${colors[2]}, ${0.35 * opacityMult})`;
        ctx.fill();
      } else {
        // Lavender - multiple small dots
        for (let i = 0; i < 5; i++) {
          ctx.beginPath();
          ctx.arc(0, -i * flower.size * 0.4, flower.size * 0.2, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${colors[0]}, ${colors[1]}, ${colors[2]}, ${0.3 * opacityMult})`;
          ctx.fill();
        }
      }

      ctx.restore();
    };

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      // Draw sky gradient
      drawSky(opacityMultiplier);

      // Draw clouds
      cloudsRef.current.forEach((cloud, i) => {
        cloud.x += cloud.speed;
        if (cloud.x > canvas.width + cloud.width) {
          cloud.x = -cloud.width;
        }
        drawCloud(cloud, opacityMultiplier);
      });

      // Draw birds
      birdsRef.current.forEach((bird, i) => {
        bird.x += bird.speed;
        bird.y += Math.sin(timeRef.current + i) * 0.1;
        if (bird.x > canvas.width + 20) {
          bird.x = -20;
          bird.y = canvas.height * (0.1 + Math.random() * 0.2);
        }
        drawBird(bird, opacityMultiplier);
      });

      // Draw hills back to front with mist between
      hillsRef.current.forEach((hill, i) => {
        drawHill(hill, opacityMultiplier);

        // Draw mist after each hill except the last
        if (i < mistRef.current.length) {
          drawMist(mistRef.current[i], opacityMultiplier);
        }
      });

      // Draw flowers in foreground
      flowersRef.current.forEach(flower => drawFlower(flower, opacityMultiplier));

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
