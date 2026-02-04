/**
 * Japanese Train Station Animation
 * Trains arriving and departing with passengers moving on platforms
 */

import { useRef, useEffect, useCallback } from 'react';

interface Train {
  x: number;
  y: number;
  length: number;
  height: number;
  speed: number;
  targetSpeed: number;
  direction: number; // 1 = right, -1 = left
  state: 'arriving' | 'stopped' | 'departing' | 'passing';
  stateTimer: number;
  colorScheme: number;
  opacity: number;
  carriages: number;
}

interface Passenger {
  x: number;
  y: number;
  targetX: number;
  size: number;
  speed: number;
  walkPhase: number;
  direction: number;
  colorScheme: number;
  opacity: number;
  state: 'waiting' | 'boarding' | 'exiting' | 'walking';
  hasUmbrella: boolean;
  hasBag: boolean;
}

interface Platform {
  y: number;
  width: number;
}

export function useTrainStation(
  canvasRef: React.RefObject<HTMLCanvasElement>,
  darkMode: boolean,
  opacity: number,
  active: boolean
) {
  const trainsRef = useRef<Train[]>([]);
  const passengersRef = useRef<Passenger[]>([]);
  const platformsRef = useRef<Platform[]>([]);
  const animationRef = useRef<number | undefined>(undefined);
  const timeRef = useRef<number>(0);

  const trainColors = darkMode
    ? [
        { body: [220, 220, 225], stripe: [200, 60, 60], window: [80, 120, 160] }, // white with red stripe
        { body: [60, 120, 60], stripe: [240, 200, 80], window: [80, 120, 160] }, // green with yellow
        { body: [60, 80, 140], stripe: [200, 200, 200], window: [80, 120, 160] }, // blue
        { body: [180, 100, 60], stripe: [240, 180, 80], window: [80, 120, 160] }, // orange (JR)
      ]
    : [
        { body: [200, 200, 205], stripe: [180, 40, 40], window: [60, 100, 140] },
        { body: [40, 100, 40], stripe: [220, 180, 60], window: [60, 100, 140] },
        { body: [40, 60, 120], stripe: [180, 180, 180], window: [60, 100, 140] },
        { body: [160, 80, 40], stripe: [220, 160, 60], window: [60, 100, 140] },
      ];

  const passengerColors = darkMode
    ? [
        { body: [80, 80, 90], skin: [220, 190, 170] },
        { body: [60, 60, 80], skin: [210, 180, 160] },
        { body: [100, 70, 70], skin: [225, 195, 175] },
        { body: [70, 90, 70], skin: [215, 185, 165] },
        { body: [90, 80, 100], skin: [220, 190, 170] },
      ]
    : [
        { body: [60, 60, 70], skin: [200, 170, 150] },
        { body: [40, 40, 60], skin: [190, 160, 140] },
        { body: [80, 50, 50], skin: [205, 175, 155] },
        { body: [50, 70, 50], skin: [195, 165, 145] },
        { body: [70, 60, 80], skin: [200, 170, 150] },
      ];

  const createTrain = useCallback((canvas: HTMLCanvasElement, forcePlatform?: number): Train => {
    const direction = Math.random() < 0.5 ? 1 : -1;
    const platformIndex = forcePlatform !== undefined ? forcePlatform : Math.floor(Math.random() * 2);

    return {
      x: direction > 0 ? -400 : canvas.width + 400,
      y: canvas.height * (0.4 + platformIndex * 0.25),
      length: 280 + Math.random() * 100,
      height: 40 + Math.random() * 15,
      speed: 0,
      targetSpeed: 3 + Math.random() * 2,
      direction,
      state: 'arriving',
      stateTimer: 0,
      colorScheme: Math.floor(Math.random() * 4),
      opacity: 0.15 + Math.random() * 0.1,
      carriages: 2 + Math.floor(Math.random() * 2),
    };
  }, []);

  const createPassenger = useCallback((canvas: HTMLCanvasElement, platformY: number): Passenger => {
    const startX = Math.random() * canvas.width;

    return {
      x: startX,
      y: platformY + 20,
      targetX: startX + (Math.random() - 0.5) * 200,
      size: 8 + Math.random() * 4,
      speed: 0.3 + Math.random() * 0.4,
      walkPhase: Math.random() * Math.PI * 2,
      direction: Math.random() < 0.5 ? 1 : -1,
      colorScheme: Math.floor(Math.random() * 5),
      opacity: 0.1 + Math.random() * 0.08,
      state: 'waiting',
      hasUmbrella: Math.random() < 0.2,
      hasBag: Math.random() < 0.4,
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

      // Create platforms
      platformsRef.current = [
        { y: canvas.height * 0.4, width: canvas.width },
        { y: canvas.height * 0.65, width: canvas.width },
      ];

      // Create initial passengers
      const passengerCount = Math.floor(canvas.width / 80) + 5;
      passengersRef.current = [];
      platformsRef.current.forEach(platform => {
        for (let i = 0; i < passengerCount / 2; i++) {
          passengersRef.current.push(createPassenger(canvas, platform.y));
        }
      });

      // Create initial trains - one on each platform
      trainsRef.current = [
        createTrain(canvas, 0),
        createTrain(canvas, 1),
      ];
    };
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const drawPlatform = (platform: Platform, opacityMult: number) => {
      const platformColor = darkMode ? [80, 75, 70] : [60, 55, 50];
      const lineColor = darkMode ? [200, 180, 60] : [180, 160, 40];
      const edgeColor = darkMode ? [100, 95, 90] : [80, 75, 70];

      // Platform surface
      ctx.fillStyle = `rgba(${platformColor[0]}, ${platformColor[1]}, ${platformColor[2]}, ${0.08 * opacityMult})`;
      ctx.fillRect(0, platform.y, platform.width, 25);

      // Yellow safety line
      ctx.fillStyle = `rgba(${lineColor[0]}, ${lineColor[1]}, ${lineColor[2]}, ${0.12 * opacityMult})`;
      ctx.fillRect(0, platform.y, platform.width, 3);

      // Platform edge
      ctx.fillStyle = `rgba(${edgeColor[0]}, ${edgeColor[1]}, ${edgeColor[2]}, ${0.1 * opacityMult})`;
      ctx.fillRect(0, platform.y - 5, platform.width, 5);
    };

    const drawTrain = (train: Train, opacityMult: number) => {
      const colors = trainColors[train.colorScheme];

      ctx.save();
      ctx.translate(train.x, train.y);
      if (train.direction < 0) ctx.scale(-1, 1);

      const carriageLength = train.length / train.carriages;

      for (let c = 0; c < train.carriages; c++) {
        const cx = c * (carriageLength + 5);

        // Main body
        ctx.beginPath();
        ctx.roundRect(cx, -train.height, carriageLength, train.height, 4);
        ctx.fillStyle = `rgba(${colors.body[0]}, ${colors.body[1]}, ${colors.body[2]}, ${train.opacity * opacityMult})`;
        ctx.fill();

        // Stripe
        ctx.fillStyle = `rgba(${colors.stripe[0]}, ${colors.stripe[1]}, ${colors.stripe[2]}, ${train.opacity * opacityMult * 0.8})`;
        ctx.fillRect(cx, -train.height * 0.4, carriageLength, train.height * 0.15);

        // Windows
        const windowCount = 5;
        const windowWidth = (carriageLength - 30) / windowCount;
        for (let w = 0; w < windowCount; w++) {
          ctx.beginPath();
          ctx.roundRect(cx + 15 + w * windowWidth, -train.height * 0.85, windowWidth * 0.7, train.height * 0.35, 2);
          ctx.fillStyle = `rgba(${colors.window[0]}, ${colors.window[1]}, ${colors.window[2]}, ${train.opacity * opacityMult * 0.6})`;
          ctx.fill();
        }

        // Doors
        const doorPositions = [0.25, 0.75];
        doorPositions.forEach(pos => {
          ctx.fillStyle = `rgba(${colors.body[0] - 20}, ${colors.body[1] - 20}, ${colors.body[2] - 20}, ${train.opacity * opacityMult})`;
          ctx.fillRect(cx + pos * carriageLength - 8, -train.height * 0.9, 16, train.height * 0.85);
        });

        // Wheels
        const wheelPositions = [0.2, 0.8];
        wheelPositions.forEach(pos => {
          ctx.beginPath();
          ctx.arc(cx + pos * carriageLength, 0, 6, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(40, 40, 45, ${train.opacity * opacityMult})`;
          ctx.fill();
        });
      }

      // Front/nose
      ctx.beginPath();
      ctx.moveTo(train.carriages * (carriageLength + 5) - 5, -train.height);
      ctx.lineTo(train.carriages * (carriageLength + 5) + 20, -train.height * 0.3);
      ctx.lineTo(train.carriages * (carriageLength + 5) + 20, 0);
      ctx.lineTo(train.carriages * (carriageLength + 5) - 5, 0);
      ctx.closePath();
      ctx.fillStyle = `rgba(${colors.body[0]}, ${colors.body[1]}, ${colors.body[2]}, ${train.opacity * opacityMult})`;
      ctx.fill();

      // Front window
      ctx.beginPath();
      ctx.moveTo(train.carriages * (carriageLength + 5), -train.height * 0.8);
      ctx.lineTo(train.carriages * (carriageLength + 5) + 12, -train.height * 0.35);
      ctx.lineTo(train.carriages * (carriageLength + 5) + 12, -train.height * 0.15);
      ctx.lineTo(train.carriages * (carriageLength + 5), -train.height * 0.15);
      ctx.closePath();
      ctx.fillStyle = `rgba(${colors.window[0]}, ${colors.window[1]}, ${colors.window[2]}, ${train.opacity * opacityMult * 0.6})`;
      ctx.fill();

      // Headlight
      ctx.beginPath();
      ctx.arc(train.carriages * (carriageLength + 5) + 15, -5, 3, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255, 240, 200, ${train.opacity * opacityMult * (train.state === 'arriving' ? 0.8 : 0.3)})`;
      ctx.fill();

      ctx.restore();
    };

    const drawPassenger = (passenger: Passenger, opacityMult: number) => {
      const colors = passengerColors[passenger.colorScheme];
      const walkBob = passenger.state === 'walking' || passenger.state === 'boarding' || passenger.state === 'exiting'
        ? Math.abs(Math.sin(timeRef.current * 8 + passenger.walkPhase)) * 2
        : 0;
      const legPhase = Math.sin(timeRef.current * 8 + passenger.walkPhase);

      ctx.save();
      ctx.translate(passenger.x, passenger.y - walkBob);
      ctx.scale(passenger.direction, 1);

      // Legs (if walking)
      if (walkBob > 0) {
        ctx.beginPath();
        ctx.moveTo(-passenger.size * 0.15, 0);
        ctx.lineTo(-passenger.size * 0.15 + legPhase * 3, passenger.size * 0.5);
        ctx.strokeStyle = `rgba(${colors.body[0]}, ${colors.body[1]}, ${colors.body[2]}, ${passenger.opacity * opacityMult})`;
        ctx.lineWidth = passenger.size * 0.2;
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(passenger.size * 0.15, 0);
        ctx.lineTo(passenger.size * 0.15 - legPhase * 3, passenger.size * 0.5);
        ctx.stroke();
      }

      // Body
      ctx.beginPath();
      ctx.ellipse(0, -passenger.size * 0.3, passenger.size * 0.3, passenger.size * 0.5, 0, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${colors.body[0]}, ${colors.body[1]}, ${colors.body[2]}, ${passenger.opacity * opacityMult})`;
      ctx.fill();

      // Head
      ctx.beginPath();
      ctx.arc(0, -passenger.size * 0.9, passenger.size * 0.25, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${colors.skin[0]}, ${colors.skin[1]}, ${colors.skin[2]}, ${passenger.opacity * opacityMult})`;
      ctx.fill();

      // Hair
      ctx.beginPath();
      ctx.arc(0, -passenger.size * 0.95, passenger.size * 0.22, Math.PI, 0);
      ctx.fillStyle = `rgba(${colors.body[0] - 30}, ${colors.body[1] - 30}, ${colors.body[2] - 30}, ${passenger.opacity * opacityMult})`;
      ctx.fill();

      // Bag
      if (passenger.hasBag) {
        ctx.beginPath();
        ctx.roundRect(passenger.size * 0.25, -passenger.size * 0.4, passenger.size * 0.3, passenger.size * 0.4, 2);
        ctx.fillStyle = `rgba(${colors.body[0] + 20}, ${colors.body[1] + 15}, ${colors.body[2] + 10}, ${passenger.opacity * opacityMult})`;
        ctx.fill();
      }

      // Umbrella
      if (passenger.hasUmbrella) {
        ctx.beginPath();
        ctx.moveTo(-passenger.size * 0.4, -passenger.size * 0.5);
        ctx.lineTo(-passenger.size * 0.4, -passenger.size * 1.8);
        ctx.strokeStyle = `rgba(100, 80, 60, ${passenger.opacity * opacityMult})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      ctx.restore();
    };

    const animate = () => {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      timeRef.current += 0.016;

      const opacityMultiplier = opacity / 50;

      // Draw platforms
      platformsRef.current.forEach(platform => drawPlatform(platform, opacityMultiplier));

      // Update and draw trains
      trainsRef.current.forEach((train, i) => {
        // State machine
        switch (train.state) {
          case 'arriving':
            train.speed += (train.targetSpeed - train.speed) * 0.02;
            train.x += train.speed * train.direction;

            // Check if should stop
            const stopX = train.direction > 0 ? canvas.width * 0.3 : canvas.width * 0.7;
            if ((train.direction > 0 && train.x > stopX) || (train.direction < 0 && train.x < stopX)) {
              train.state = 'stopped';
              train.stateTimer = 200 + Math.random() * 200;

              // Passengers exit and board
              passengersRef.current.forEach(p => {
                if (Math.abs(p.y - train.y - 20) < 30) {
                  if (Math.random() < 0.3) {
                    p.state = 'exiting';
                    p.targetX = p.x + (Math.random() - 0.5) * 200;
                  } else if (Math.random() < 0.3) {
                    p.state = 'boarding';
                    p.targetX = train.x + train.length / 2;
                  }
                }
              });
            }
            break;

          case 'stopped':
            train.speed *= 0.9;
            train.stateTimer--;
            if (train.stateTimer <= 0) {
              train.state = 'departing';
            }
            break;

          case 'departing':
            train.speed += train.targetSpeed * 0.01;
            train.x += train.speed * train.direction;

            if (train.x < -train.length - 50 || train.x > canvas.width + train.length + 50) {
              trainsRef.current[i] = createTrain(canvas);
            }
            break;
        }

        drawTrain(train, opacityMultiplier);
      });

      // Spawn new trains - ensure each platform has a train
      const platform0HasTrain = trainsRef.current.some(t => Math.abs(t.y - canvas.height * 0.4) < 20);
      const platform1HasTrain = trainsRef.current.some(t => Math.abs(t.y - canvas.height * 0.65) < 20);

      if (!platform0HasTrain && Math.random() < 0.008) {
        trainsRef.current.push(createTrain(canvas, 0));
      }
      if (!platform1HasTrain && Math.random() < 0.008) {
        trainsRef.current.push(createTrain(canvas, 1));
      }

      // Update and draw passengers
      passengersRef.current.forEach((passenger, i) => {
        switch (passenger.state) {
          case 'waiting':
            // Occasionally start walking
            if (Math.random() < 0.002) {
              passenger.state = 'walking';
              passenger.targetX = passenger.x + (Math.random() - 0.5) * 150;
            }
            break;

          case 'walking':
          case 'exiting':
            const dx = passenger.targetX - passenger.x;
            if (Math.abs(dx) > 2) {
              passenger.direction = dx > 0 ? 1 : -1;
              passenger.x += passenger.speed * passenger.direction;
            } else {
              passenger.state = 'waiting';
            }
            break;

          case 'boarding':
            // Move toward train
            const bx = passenger.targetX - passenger.x;
            if (Math.abs(bx) > 5) {
              passenger.direction = bx > 0 ? 1 : -1;
              passenger.x += passenger.speed * passenger.direction;
            } else {
              // Boarded - respawn as new passenger
              const platformY = platformsRef.current[Math.floor(Math.random() * platformsRef.current.length)].y;
              passengersRef.current[i] = createPassenger(canvas, platformY);
            }
            break;
        }

        // Keep passengers on screen
        if (passenger.x < 0) passenger.x = canvas.width;
        if (passenger.x > canvas.width) passenger.x = 0;

        drawPassenger(passenger, opacityMultiplier);
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
  }, [canvasRef, darkMode, opacity, active, createTrain, createPassenger]);
}
