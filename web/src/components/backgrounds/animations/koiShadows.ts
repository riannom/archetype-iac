/**
 * Koi Shadows Animation
 * Graceful koi fish rendered as ink brush strokes
 */

import { useRef, useEffect } from 'react';

interface KoiFish {
  x: number;
  y: number;
  size: number;
  angle: number;
  speed: number;
  tailPhase: number;
  bodyPhase: number;
  opacity: number;
  turnSpeed: number;
  targetAngle: number;
  colorScheme: number;
}

export function useKoiShadows(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const koiRef = useRef<KoiFish[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

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

    koiRef.current = Array.from({ length: 7 }, (_, i) => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      size: 35 + Math.random() * 20,
      angle: Math.random() * Math.PI * 2,
      speed: 0.4 + Math.random() * 0.4,
      tailPhase: Math.random() * Math.PI * 2,
      bodyPhase: Math.random() * Math.PI * 2,
      opacity: 0.12 + Math.random() * 0.08,
      turnSpeed: 0.008 + Math.random() * 0.015,
      targetAngle: Math.random() * Math.PI * 2,
      colorScheme: i % 5,
    }));

    const getKoiColors = (scheme: number, baseOpacity: number) => {
      const o = baseOpacity;
      if (darkMode) {
        switch (scheme) {
          case 0: return { body: `rgba(240, 200, 180, ${o})`, accent: `rgba(220, 130, 100, ${o * 0.7})`, spot: `rgba(200, 100, 80, ${o * 0.5})` };
          case 1: return { body: `rgba(220, 220, 215, ${o})`, accent: `rgba(200, 200, 195, ${o * 0.6})`, spot: `rgba(180, 180, 175, ${o * 0.4})` };
          case 2: return { body: `rgba(230, 210, 200, ${o})`, accent: `rgba(200, 120, 100, ${o * 0.6})`, spot: `rgba(80, 70, 65, ${o * 0.5})` };
          case 3: return { body: `rgba(230, 200, 140, ${o})`, accent: `rgba(220, 180, 100, ${o * 0.7})`, spot: `rgba(200, 160, 80, ${o * 0.5})` };
          default: return { body: `rgba(180, 170, 165, ${o})`, accent: `rgba(160, 150, 145, ${o * 0.7})`, spot: `rgba(140, 130, 125, ${o * 0.5})` };
        }
      } else {
        switch (scheme) {
          case 0: return { body: `rgba(180, 100, 70, ${o})`, accent: `rgba(200, 80, 50, ${o * 0.6})`, spot: `rgba(180, 60, 40, ${o * 0.4})` };
          case 1: return { body: `rgba(90, 85, 80, ${o})`, accent: `rgba(100, 95, 90, ${o * 0.5})`, spot: `rgba(110, 105, 100, ${o * 0.3})` };
          case 2: return { body: `rgba(140, 80, 60, ${o})`, accent: `rgba(160, 60, 40, ${o * 0.5})`, spot: `rgba(40, 35, 30, ${o * 0.6})` };
          case 3: return { body: `rgba(160, 120, 50, ${o})`, accent: `rgba(180, 130, 40, ${o * 0.6})`, spot: `rgba(170, 110, 30, ${o * 0.4})` };
          default: return { body: `rgba(50, 45, 40, ${o})`, accent: `rgba(60, 55, 50, ${o * 0.6})`, spot: `rgba(70, 65, 60, ${o * 0.4})` };
        }
      }
    };

    const drawKoi = (koi: KoiFish) => {
      ctx.save();
      ctx.translate(koi.x, koi.y);
      ctx.rotate(koi.angle);

      const opacityMultiplier = opacity / 50;
      const baseOpacity = koi.opacity * opacityMultiplier;
      const colors = getKoiColors(koi.colorScheme, baseOpacity);

      const s = koi.size;

      // Propagating wave - phase delay increases toward tail, amplitude increases
      const waveAt = (pos: number) => {
        const phaseDelay = pos * 1.2;
        const ampScale = pos * pos;
        return Math.sin(koi.bodyPhase - phaseDelay) * 3 * ampScale;
      };

      const wave20 = waveAt(0.2);
      const wave40 = waveAt(0.4);
      const wave60 = waveAt(0.6);
      const wave80 = waveAt(0.8);
      const wave100 = waveAt(1.0);

      // Main body with propagating wave - tapered toward tail
      ctx.beginPath();
      ctx.moveTo(s * 0.9, 0);
      // Upper contour - widest at front, tapering to narrow caudal peduncle
      ctx.bezierCurveTo(s * 0.7, -s * 0.14 + wave20 * 0.3, s * 0.4, -s * 0.20 + wave20, s * 0.1, -s * 0.18 + wave40);
      ctx.bezierCurveTo(-s * 0.15, -s * 0.14 + wave60, -s * 0.4, -s * 0.08 + wave80, -s * 0.65, -s * 0.03 + wave100);
      // Narrow caudal peduncle
      ctx.lineTo(-s * 0.65, s * 0.03 + wave100);
      // Lower contour - mirror
      ctx.bezierCurveTo(-s * 0.4, s * 0.08 + wave80, -s * 0.15, s * 0.14 + wave60, s * 0.1, s * 0.18 + wave40);
      ctx.bezierCurveTo(s * 0.4, s * 0.20 + wave20, s * 0.7, s * 0.14 + wave20 * 0.3, s * 0.9, 0);
      ctx.closePath();

      const bodyGradient = ctx.createLinearGradient(-s * 0.75, 0, s * 0.9, 0);
      bodyGradient.addColorStop(0, colors.accent);
      bodyGradient.addColorStop(0.4, colors.body);
      bodyGradient.addColorStop(0.8, colors.body);
      bodyGradient.addColorStop(1, colors.accent);
      ctx.fillStyle = bodyGradient;
      ctx.fill();

      // Pattern spots - follow propagating wave
      if (koi.colorScheme !== 4) {
        ctx.beginPath();
        ctx.ellipse(s * 0.1, -s * 0.05 + wave40 * 0.5, s * 0.2, s * 0.08, 0.2, 0, Math.PI * 2);
        ctx.fillStyle = colors.spot;
        ctx.fill();

        ctx.beginPath();
        ctx.ellipse(-s * 0.25, s * 0.03 + wave60 * 0.5, s * 0.15, s * 0.06, -0.1, 0, Math.PI * 2);
        ctx.fillStyle = colors.spot;
        ctx.fill();
      }

      // Tail fin - thin overhead view, continues the wave
      const tailWave = waveAt(1.15);
      const tailTip = waveAt(1.4);
      const tailSpread = s * 0.03; // Very thin from overhead

      ctx.beginPath();
      ctx.moveTo(-s * 0.63, wave100);
      // Thin tail that follows the wave
      ctx.quadraticCurveTo(-s * 0.9, tailWave - tailSpread, -s * 1.15, tailTip - tailSpread);
      ctx.quadraticCurveTo(-s * 1.2, tailTip, -s * 1.15, tailTip + tailSpread);
      ctx.quadraticCurveTo(-s * 0.9, tailWave + tailSpread, -s * 0.63, wave100);
      ctx.closePath();

      const tailGradient = ctx.createLinearGradient(-s * 0.72, 0, -s * 1.2, 0);
      tailGradient.addColorStop(0, colors.body);
      tailGradient.addColorStop(0.5, colors.accent);
      tailGradient.addColorStop(1, `rgba(0, 0, 0, 0)`);
      ctx.fillStyle = tailGradient;
      ctx.fill();

      // Dorsal fin - follows propagating wave
      const dorsalWave = waveAt(0.5);
      ctx.beginPath();
      ctx.moveTo(s * 0.3, -s * 0.2 + wave40);
      ctx.bezierCurveTo(s * 0.15, -s * 0.35 + dorsalWave, -s * 0.1, -s * 0.38 + wave60, -s * 0.25, -s * 0.18 + wave80);
      ctx.bezierCurveTo(-s * 0.1, -s * 0.22 + wave60, s * 0.15, -s * 0.22 + wave40, s * 0.3, -s * 0.2 + wave40);
      ctx.closePath();
      ctx.fillStyle = colors.accent;
      ctx.fill();

      // Pectoral fins
      const finWave = Math.sin(koi.tailPhase * 0.7) * 3;

      ctx.beginPath();
      ctx.moveTo(s * 0.35, s * 0.15);
      ctx.bezierCurveTo(s * 0.5, s * 0.35 + finWave, s * 0.3, s * 0.45 + finWave, s * 0.1, s * 0.35 + finWave * 0.5);
      ctx.bezierCurveTo(s * 0.2, s * 0.25, s * 0.3, s * 0.18, s * 0.35, s * 0.15);
      ctx.closePath();
      ctx.fillStyle = colors.accent;
      ctx.fill();

      ctx.beginPath();
      ctx.moveTo(s * 0.35, -s * 0.15);
      ctx.bezierCurveTo(s * 0.5, -s * 0.35 - finWave, s * 0.3, -s * 0.45 - finWave, s * 0.1, -s * 0.35 - finWave * 0.5);
      ctx.bezierCurveTo(s * 0.2, -s * 0.25, s * 0.3, -s * 0.18, s * 0.35, -s * 0.15);
      ctx.closePath();
      ctx.fillStyle = colors.accent;
      ctx.fill();

      // Eye
      ctx.beginPath();
      ctx.arc(s * 0.6, -s * 0.05, s * 0.04, 0, Math.PI * 2);
      ctx.fillStyle = darkMode ? `rgba(40, 35, 30, ${baseOpacity * 0.6})` : `rgba(30, 25, 20, ${baseOpacity * 0.5})`;
      ctx.fill();

      ctx.restore();
    };

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      koiRef.current.forEach((koi) => {
        koi.tailPhase += 0.06;
        koi.bodyPhase += 0.03;

        if (Math.random() < 0.008) {
          koi.targetAngle = koi.angle + (Math.random() - 0.5) * Math.PI * 0.4;
        }

        const angleDiff = koi.targetAngle - koi.angle;
        koi.angle += angleDiff * koi.turnSpeed;

        const speedVar = 1 + Math.sin(koi.bodyPhase * 0.5) * 0.1;
        koi.x += Math.cos(koi.angle) * koi.speed * speedVar;
        koi.y += Math.sin(koi.angle) * koi.speed * speedVar;

        const margin = koi.size * 2;
        if (koi.x < -margin) koi.x = canvas.width + margin * 0.5;
        if (koi.x > canvas.width + margin) koi.x = -margin * 0.5;
        if (koi.y < -margin) koi.y = canvas.height + margin * 0.5;
        if (koi.y > canvas.height + margin) koi.y = -margin * 0.5;

        drawKoi(koi);
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
  }, [canvasRef, darkMode, opacity, active]);
}
