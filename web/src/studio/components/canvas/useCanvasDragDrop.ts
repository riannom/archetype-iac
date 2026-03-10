import { useCallback } from 'react';
import { DeviceModel } from '../../types';

interface UseCanvasDragDropArgs {
  containerRef: React.RefObject<HTMLDivElement | null>;
  offset: { x: number; y: number };
  zoom: number;
  onDropDevice?: (model: DeviceModel, x: number, y: number) => void;
  onDropExternalNetwork?: (x: number, y: number) => void;
}

export function useCanvasDragDrop({
  containerRef,
  offset,
  zoom,
  onDropDevice,
  onDropExternalNetwork,
}: UseCanvasDragDropArgs) {
  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('application/x-archetype-device') ||
        e.dataTransfer.types.includes('application/x-archetype-external')) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left - offset.x) / zoom;
    const y = (e.clientY - rect.top - offset.y) / zoom;

    const deviceData = e.dataTransfer.getData('application/x-archetype-device');
    if (deviceData && onDropDevice) {
      try {
        const model = JSON.parse(deviceData) as DeviceModel;
        onDropDevice(model, x, y);
      } catch { /* ignore parse errors */ }
      return;
    }

    const externalData = e.dataTransfer.getData('application/x-archetype-external');
    if (externalData && onDropExternalNetwork) {
      onDropExternalNetwork(x, y);
    }
  }, [containerRef, offset, zoom, onDropDevice, onDropExternalNetwork]);

  return { handleDragOver, handleDrop };
}
