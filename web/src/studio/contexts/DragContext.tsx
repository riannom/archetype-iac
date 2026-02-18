import React, { createContext, useContext, useState, useCallback, ReactNode } from 'react';
import { apiRequest } from '../../api';

interface DragState {
  isDragging: boolean;
  draggedImageId: string | null;
  draggedImageData: DraggedImage | null;
  dragOverDeviceId: string | null;
  isValidTarget: boolean;
}

interface DraggedImage {
  id: string;
  kind: string;
  reference: string;
  filename?: string;
  device_id?: string | null;
  version?: string | null;
  vendor?: string | null;
  size_bytes?: number | null;
}

interface DragContextValue {
  dragState: DragState;
  startDrag: (image: DraggedImage) => void;
  endDrag: () => void;
  setDragOverDevice: (deviceId: string | null, isValid: boolean) => void;
  assignImageToDevice: (imageId: string, deviceId: string, isDefault?: boolean) => Promise<void>;
  unassignImage: (imageId: string, deviceId?: string) => Promise<void>;
  deleteImage: (imageId: string) => Promise<void>;
}

const DragContext = createContext<DragContextValue | null>(null);

export const useDragContext = () => {
  const context = useContext(DragContext);
  if (!context) {
    throw new Error('useDragContext must be used within a DragProvider');
  }
  return context;
};

interface DragProviderProps {
  children: ReactNode;
  onImageAssigned?: () => void;
}

export const DragProvider: React.FC<DragProviderProps> = ({ children, onImageAssigned }) => {
  const [dragState, setDragState] = useState<DragState>({
    isDragging: false,
    draggedImageId: null,
    draggedImageData: null,
    dragOverDeviceId: null,
    isValidTarget: false,
  });

  const startDrag = useCallback((image: DraggedImage) => {
    setDragState({
      isDragging: true,
      draggedImageId: image.id,
      draggedImageData: image,
      dragOverDeviceId: null,
      isValidTarget: false,
    });
  }, []);

  const endDrag = useCallback(() => {
    setDragState({
      isDragging: false,
      draggedImageId: null,
      draggedImageData: null,
      dragOverDeviceId: null,
      isValidTarget: false,
    });
  }, []);

  const setDragOverDevice = useCallback((deviceId: string | null, isValid: boolean) => {
    setDragState((prev) => ({
      ...prev,
      dragOverDeviceId: deviceId,
      isValidTarget: isValid,
    }));
  }, []);

  const assignImageToDevice = useCallback(async (imageId: string, deviceId: string, isDefault = false) => {
    await apiRequest(`/images/library/${encodeURIComponent(imageId)}/assign`, {
      method: 'POST',
      body: JSON.stringify({ device_id: deviceId, is_default: isDefault }),
    });
    onImageAssigned?.();
  }, [onImageAssigned]);

  const unassignImage = useCallback(async (imageId: string, deviceId?: string) => {
    await apiRequest(`/images/library/${encodeURIComponent(imageId)}/unassign`, {
      method: 'POST',
      body: deviceId ? JSON.stringify({ device_id: deviceId }) : undefined,
    });
    onImageAssigned?.();
  }, [onImageAssigned]);

  const deleteImage = useCallback(async (imageId: string) => {
    await apiRequest(`/images/library/${encodeURIComponent(imageId)}`, {
      method: 'DELETE',
    });
    onImageAssigned?.();
  }, [onImageAssigned]);

  return (
    <DragContext.Provider
      value={{
        dragState,
        startDrag,
        endDrag,
        setDragOverDevice,
        assignImageToDevice,
        unassignImage,
        deleteImage,
      }}
    >
      {children}
    </DragContext.Provider>
  );
};

// Helper hook for handling native HTML5 drag events
export const useDragHandlers = (imageData: DraggedImage) => {
  const { startDrag, endDrag } = useDragContext();

  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      e.dataTransfer.setData('application/x-image-id', imageData.id);
      e.dataTransfer.effectAllowed = 'move';
      startDrag(imageData);
    },
    [imageData, startDrag]
  );

  const handleDragEnd = useCallback(() => {
    endDrag();
  }, [endDrag]);

  return { handleDragStart, handleDragEnd };
};

// Helper hook for handling drop targets
export const useDropHandlers = (deviceId: string, onDrop?: () => void) => {
  const { dragState, setDragOverDevice, assignImageToDevice, endDrag } = useDragContext();

  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      setDragOverDevice(deviceId, true);
    },
    [deviceId, setDragOverDevice]
  );

  const handleDragLeave = useCallback(() => {
    setDragOverDevice(null, false);
  }, [setDragOverDevice]);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      const imageId = e.dataTransfer.getData('application/x-image-id');
      if (imageId) {
        try {
          await assignImageToDevice(imageId, deviceId);
          onDrop?.();
        } catch (error) {
          console.error('Failed to assign image:', error);
        }
      }
      endDrag();
    },
    [deviceId, assignImageToDevice, endDrag, onDrop]
  );

  const isDropTarget = dragState.isDragging && dragState.dragOverDeviceId === deviceId;

  return {
    handleDragOver,
    handleDragLeave,
    handleDrop,
    isDropTarget,
    isDragging: dragState.isDragging,
  };
};
