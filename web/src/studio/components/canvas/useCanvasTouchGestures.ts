import { useCallback, useRef } from 'react';

interface UseCanvasTouchGesturesArgs {
  containerRef: React.RefObject<HTMLDivElement | null>;
  zoom: number;
  offset: { x: number; y: number };
  setOffset: React.Dispatch<React.SetStateAction<{ x: number; y: number }>>;
  setIsPanning: React.Dispatch<React.SetStateAction<boolean>>;
  editingText: { id: string; x: number; y: number } | null;
  clampZoom: (value: number) => number;
  applyZoomAtPoint: (
    newZoom: number,
    clientX: number,
    clientY: number,
    baseZoom: number,
    baseOffset: { x: number; y: number }
  ) => { x: number; y: number };
}

export function useCanvasTouchGestures({
  containerRef,
  zoom,
  offset,
  setOffset,
  setIsPanning,
  editingText,
  clampZoom,
  applyZoomAtPoint,
}: UseCanvasTouchGesturesArgs) {
  const touchPanRef = useRef<{ x: number; y: number } | null>(null);
  const pinchRef = useRef<{
    distance: number;
    zoom: number;
    offset: { x: number; y: number };
  } | null>(null);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (!containerRef.current) return;
    if (e.touches.length >= 2) {
      const [first, second] = [e.touches[0], e.touches[1]];
      const distance = Math.hypot(second.clientX - first.clientX, second.clientY - first.clientY);
      pinchRef.current = { distance, zoom, offset };
      touchPanRef.current = null;
      setIsPanning(false);
      e.preventDefault();
      return;
    }

    if (editingText) return;

    if (e.touches.length === 1) {
      const touch = e.touches[0];
      touchPanRef.current = { x: touch.clientX, y: touch.clientY };
      pinchRef.current = null;
      setIsPanning(true);
      e.preventDefault();
    }
  }, [containerRef, zoom, offset, editingText, setIsPanning]);

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (e.touches.length >= 2) {
      const [first, second] = [e.touches[0], e.touches[1]];
      const distance = Math.hypot(second.clientX - first.clientX, second.clientY - first.clientY);
      const midpointX = (first.clientX + second.clientX) / 2;
      const midpointY = (first.clientY + second.clientY) / 2;
      const pinch = pinchRef.current ?? { distance, zoom, offset };
      const newZoom = clampZoom(pinch.zoom * (distance / Math.max(pinch.distance, 1)));
      const newOffset = applyZoomAtPoint(newZoom, midpointX, midpointY, pinch.zoom, pinch.offset);
      pinchRef.current = { distance, zoom: newZoom, offset: newOffset };
      setIsPanning(false);
      e.preventDefault();
      return;
    }

    if (e.touches.length === 1 && touchPanRef.current) {
      const touch = e.touches[0];
      const dx = touch.clientX - touchPanRef.current.x;
      const dy = touch.clientY - touchPanRef.current.y;
      setOffset(prev => ({ x: prev.x + dx, y: prev.y + dy }));
      touchPanRef.current = { x: touch.clientX, y: touch.clientY };
      setIsPanning(true);
      e.preventDefault();
    }
  }, [zoom, offset, clampZoom, applyZoomAtPoint, setOffset, setIsPanning]);

  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    if (e.touches.length >= 2) {
      const [first, second] = [e.touches[0], e.touches[1]];
      pinchRef.current = {
        distance: Math.hypot(second.clientX - first.clientX, second.clientY - first.clientY),
        zoom,
        offset,
      };
      return;
    }

    if (e.touches.length === 1) {
      const touch = e.touches[0];
      touchPanRef.current = { x: touch.clientX, y: touch.clientY };
      pinchRef.current = null;
      setIsPanning(true);
      return;
    }

    touchPanRef.current = null;
    pinchRef.current = null;
    setIsPanning(false);
  }, [zoom, offset, setIsPanning]);

  return { handleTouchStart, handleTouchMove, handleTouchEnd };
}
