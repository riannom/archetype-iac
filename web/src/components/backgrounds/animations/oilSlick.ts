/**
 * Oil Slick Animation
 *
 * Iridescent swirling patterns with rainbow interference colors,
 * like oil on water. Slow morphing shapes with prismatic edges.
 */

import { useEffect, useRef } from 'react';

interface OilBlob {
  x: number;
  y: number;
  radius: number;
  phase: number;
  phaseSpeed: number;
  hueOffset: number;
  morphPhases: number[];
  morphSpeeds: number[];
}

export function useOilSlick(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
): void {
  const blobsRef = useRef<OilBlob[]>([]);
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
      initializeBlobs();
    };

    const initializeBlobs = () => {
      const { width, height } = canvas;
      blobsRef.current = [];

      const blobCount = 4 + Math.floor(Math.random() * 3);
      for (let i = 0; i < blobCount; i++) {
        // Generate morph phases for organic shape distortion
        const morphCount = 5 + Math.floor(Math.random() * 4);
        const morphPhases: number[] = [];
        const morphSpeeds: number[] = [];
        for (let m = 0; m < morphCount; m++) {
          morphPhases.push(Math.random() * Math.PI * 2);
          morphSpeeds.push(0.005 + Math.random() * 0.01);
        }

        blobsRef.current.push({
          x: width * 0.1 + Math.random() * width * 0.8,
          y: height * 0.1 + Math.random() * height * 0.8,
          radius: 100 + Math.random() * 150,
          phase: Math.random() * Math.PI * 2,
          phaseSpeed: 0.003 + Math.random() * 0.003,
          hueOffset: Math.random() * 360,
          morphPhases,
          morphSpeeds,
        });
      }
    };

    const getBlobPath = (blob: OilBlob, scale: number = 1): Path2D => {
      const path = new Path2D();
      const points = 64;

      for (let i = 0; i <= points; i++) {
        const angle = (i / points) * Math.PI * 2;

        // Apply multiple morph frequencies for organic shape
        let radiusOffset = 0;
        blob.morphPhases.forEach((phase, idx) => {
          const frequency = idx + 2;
          const amplitude = 0.15 / (idx + 1);
          radiusOffset += Math.sin(angle * frequency + phase) * amplitude;
        });

        const r = blob.radius * scale * (1 + radiusOffset);
        const x = blob.x + Math.cos(angle) * r;
        const y = blob.y + Math.sin(angle) * r;

        if (i === 0) {
          path.moveTo(x, y);
        } else {
          path.lineTo(x, y);
        }
      }

      path.closePath();
      return path;
    };

    const drawBlob = (blob: OilBlob) => {
      const opacityMult = (opacity / 50) * 0.5;

      // Draw multiple layers with different hue offsets for iridescence
      const layers = 8;
      for (let layer = layers - 1; layer >= 0; layer--) {
        const layerScale = 0.3 + (layer / layers) * 0.7;
        const path = getBlobPath(blob, layerScale);

        // Iridescent hue cycling based on position and layer
        const baseHue = (blob.hueOffset + blob.phase * 30 + layer * 40) % 360;

        // Create gradient for this layer
        const gradient = ctx.createRadialGradient(
          blob.x - blob.radius * 0.2, blob.y - blob.radius * 0.2, 0,
          blob.x, blob.y, blob.radius * layerScale
        );

        const layerOpacity = opacityMult * (0.3 + (layer / layers) * 0.3);

        if (darkMode) {
          gradient.addColorStop(0, `hsla(${baseHue}, 60%, 55%, ${layerOpacity})`);
          gradient.addColorStop(0.3, `hsla(${(baseHue + 30) % 360}, 55%, 50%, ${layerOpacity * 0.8})`);
          gradient.addColorStop(0.6, `hsla(${(baseHue + 60) % 360}, 50%, 45%, ${layerOpacity * 0.5})`);
          gradient.addColorStop(1, `hsla(${(baseHue + 90) % 360}, 45%, 40%, ${layerOpacity * 0.2})`);
        } else {
          gradient.addColorStop(0, `hsla(${baseHue}, 70%, 65%, ${layerOpacity})`);
          gradient.addColorStop(0.3, `hsla(${(baseHue + 30) % 360}, 65%, 60%, ${layerOpacity * 0.8})`);
          gradient.addColorStop(0.6, `hsla(${(baseHue + 60) % 360}, 60%, 55%, ${layerOpacity * 0.5})`);
          gradient.addColorStop(1, `hsla(${(baseHue + 90) % 360}, 55%, 50%, ${layerOpacity * 0.2})`);
        }

        ctx.fillStyle = gradient;
        ctx.fill(path);
      }

      // Highlight edge for prismatic effect
      const edgePath = getBlobPath(blob, 1);
      const edgeGradient = ctx.createRadialGradient(
        blob.x, blob.y, blob.radius * 0.8,
        blob.x, blob.y, blob.radius * 1.1
      );
      const edgeHue = (blob.hueOffset + blob.phase * 50) % 360;
      edgeGradient.addColorStop(0, 'transparent');
      edgeGradient.addColorStop(0.5, `hsla(${edgeHue}, 80%, 70%, ${opacityMult * 0.15})`);
      edgeGradient.addColorStop(1, 'transparent');

      ctx.strokeStyle = edgeGradient;
      ctx.lineWidth = 3;
      ctx.stroke(edgePath);

      // Inner shimmer
      const shimmerX = blob.x + Math.sin(blob.phase * 2) * blob.radius * 0.2;
      const shimmerY = blob.y + Math.cos(blob.phase * 2) * blob.radius * 0.2;
      const shimmerGradient = ctx.createRadialGradient(
        shimmerX, shimmerY, 0,
        shimmerX, shimmerY, blob.radius * 0.4
      );
      shimmerGradient.addColorStop(0, `hsla(${(blob.hueOffset + 180) % 360}, 70%, 80%, ${opacityMult * 0.2})`);
      shimmerGradient.addColorStop(1, 'transparent');

      ctx.fillStyle = shimmerGradient;
      ctx.beginPath();
      ctx.arc(shimmerX, shimmerY, blob.radius * 0.4, 0, Math.PI * 2);
      ctx.fill();
    };

    const animate = () => {
      const { width, height } = canvas;
      timeRef.current += 16;

      // Clear
      ctx.fillStyle = darkMode ? 'rgba(10, 15, 20, 1)' : 'rgba(245, 248, 252, 1)';
      ctx.fillRect(0, 0, width, height);

      // Update and draw blobs
      blobsRef.current.forEach((blob) => {
        blob.phase += blob.phaseSpeed;
        blob.hueOffset += 0.1; // Slow hue cycling

        // Update morph phases
        blob.morphPhases = blob.morphPhases.map((phase, idx) => phase + blob.morphSpeeds[idx]);

        // Slow drift
        blob.x += Math.sin(blob.phase * 0.5) * 0.2;
        blob.y += Math.cos(blob.phase * 0.3) * 0.15;

        // Wrap around screen
        if (blob.x < -blob.radius) blob.x = width + blob.radius;
        if (blob.x > width + blob.radius) blob.x = -blob.radius;
        if (blob.y < -blob.radius) blob.y = height + blob.radius;
        if (blob.y > height + blob.radius) blob.y = -blob.radius;

        drawBlob(blob);
      });

      // Subtle overall shimmer overlay
      const shimmerOverlay = ctx.createRadialGradient(
        width / 2 + Math.sin(timeRef.current * 0.001) * 100,
        height / 2 + Math.cos(timeRef.current * 0.0008) * 100,
        0,
        width / 2, height / 2, Math.max(width, height) * 0.5
      );
      const overlayHue = (timeRef.current * 0.01) % 360;
      shimmerOverlay.addColorStop(0, `hsla(${overlayHue}, 50%, 70%, 0.02)`);
      shimmerOverlay.addColorStop(1, 'transparent');
      ctx.fillStyle = shimmerOverlay;
      ctx.fillRect(0, 0, width, height);

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
