import { useCallback, useMemo } from 'react';
import { DeviceModel, DeviceNode, ExternalNetworkNode, Node, isExternalNetworkNode } from '../types';
import { generateContainerName } from '../studioUtils';
import {
  buildImageCompatibilityAliasMap,
  getAllowedInstantiableImageKinds,
  imageMatchesDeviceId,
  isInstantiableImageKind,
  requiresRunnableImage,
} from '../../utils/deviceModels';
import { ImageLibraryEntry } from '../types';
import type { NotificationLevel, Notification } from '../../types/notifications';

interface UseAddDeviceOptions {
  nodes: Node[];
  setNodes: React.Dispatch<React.SetStateAction<Node[]>>;
  setSelectedId: (id: string | null) => void;
  triggerTopologySave: () => void;
  deviceModels: DeviceModel[];
  imageLibrary: ImageLibraryEntry[];
  addNotification: (level: NotificationLevel, title: string, message?: string, options?: Partial<Notification>) => void;
}

export function useAddDevice({
  nodes,
  setNodes,
  setSelectedId,
  triggerTopologySave,
  deviceModels,
  imageLibrary,
  addNotification,
}: UseAddDeviceOptions) {
  const imageCompatibilityAliases = useMemo(
    () => buildImageCompatibilityAliasMap(deviceModels),
    [deviceModels]
  );

  const hasInstantiableImageForModel = useCallback((model: DeviceModel): boolean => {
    const allowedKinds = getAllowedInstantiableImageKinds(model);

    return imageLibrary.some((img) => {
      if (!isInstantiableImageKind(img.kind)) {
        return false;
      }
      const imageKind = (img.kind || '').toLowerCase();
      if (!allowedKinds.has(imageKind)) {
        return false;
      }
      return imageMatchesDeviceId(img, model.id, imageCompatibilityAliases);
    });
  }, [imageLibrary, imageCompatibilityAliases]);

  const handleAddDevice = useCallback((model: DeviceModel, x?: number, y?: number) => {
    if (requiresRunnableImage(model) && !hasInstantiableImageForModel(model)) {
      addNotification(
        'warning',
        'No runnable image assigned',
        `${model.name} has no associated Docker or qcow2 image and cannot be instantiated.`,
      );
      return;
    }

    const id = Math.random().toString(36).slice(2, 9);
    const displayName = `${model.id.toUpperCase()}-${nodes.length + 1}`;
    const newNode: DeviceNode = {
      id,
      nodeType: 'device',
      name: displayName,
      container_name: generateContainerName(displayName),
      type: model.type,
      model: model.id,
      version: model.versions[0],
      x: x ?? 300 + Math.random() * 50,
      y: y ?? 200 + Math.random() * 50,
      cpu: model.cpu || 1,
      memory: model.memory || 1024,
    };
    setNodes((prev) => [...prev, newNode]);
    setSelectedId(id);
    setTimeout(() => triggerTopologySave(), 100);
  }, [nodes, setNodes, setSelectedId, triggerTopologySave, hasInstantiableImageForModel, addNotification]);

  const handleAddExternalNetwork = useCallback((x?: number, y?: number) => {
    const id = Math.random().toString(36).slice(2, 9);
    const extNetCount = nodes.filter((n) => isExternalNetworkNode(n)).length;
    const newNode: ExternalNetworkNode = {
      id,
      nodeType: 'external',
      name: `External-${extNetCount + 1}`,
      x: x ?? 350 + Math.random() * 50,
      y: y ?? 250 + Math.random() * 50,
    };
    setNodes((prev) => [...prev, newNode]);
    setSelectedId(id);
    setTimeout(() => triggerTopologySave(), 100);
  }, [nodes, setNodes, setSelectedId, triggerTopologySave]);

  return {
    handleAddDevice,
    handleAddExternalNetwork,
    imageCompatibilityAliases,
    hasInstantiableImageForModel,
  };
}
