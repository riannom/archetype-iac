import React, { useState } from 'react';
import { ImageLibraryEntry, ImageHostStatus, DeviceModel } from '../types';
import { useDragHandlers, useDragContext } from '../contexts/DragContext';
import { formatSize, formatDate } from '../../utils/format';
import { apiRequest } from '../../api';

interface ImageCardProps {
  image: ImageLibraryEntry;
  device?: DeviceModel;
  onUnassign?: () => void;
  onSetDefault?: () => void;
  onDelete?: () => void;
  onSync?: () => void;
  compact?: boolean;
  showSyncStatus?: boolean;
  isPending?: boolean;
  pendingMessage?: string;
}

const ImageCard: React.FC<ImageCardProps> = ({
  image,
  device,
  onUnassign,
  onSetDefault,
  onDelete,
  onSync,
  compact = false,
  showSyncStatus = false,
  isPending = false,
  pendingMessage,
}) => {
  const { dragState } = useDragContext();
  const { handleDragStart, handleDragEnd } = useDragHandlers({
    id: image.id,
    kind: image.kind,
    reference: image.reference,
    filename: image.filename,
    device_id: image.device_id,
    version: image.version,
    vendor: image.vendor,
    size_bytes: image.size_bytes,
  });

  const [syncing, setSyncing] = useState(false);

  const isDragging = dragState.draggedImageId === image.id;

  const getKindIcon = () => {
    if (image.kind === 'docker') return 'fa-brands fa-docker';
    if (image.kind === 'iol') return 'fa-solid fa-microchip';
    return 'fa-solid fa-hard-drive';
  };

  const getKindColor = () => {
    if (image.kind === 'docker') return 'text-blue-500';
    if (image.kind === 'iol') return 'text-purple-500';
    return 'text-orange-500';
  };

  const getSyncStatusSummary = () => {
    if (!image.host_status || image.host_status.length === 0) return null;
    const synced = image.host_status.filter(h => h.status === 'synced').length;
    const failed = image.host_status.filter(h => h.status === 'failed').length;
    const syncing = image.host_status.filter(h => h.status === 'syncing').length;
    const total = image.host_status.length;

    if (syncing > 0) return { icon: 'fa-sync fa-spin', color: 'text-blue-500', label: 'Syncing' };
    if (failed > 0) return { icon: 'fa-exclamation-triangle', color: 'text-red-500', label: `${failed} failed` };
    if (synced === total) return { icon: 'fa-check-circle', color: 'text-green-500', label: 'All synced' };
    if (synced > 0) return { icon: 'fa-circle-half-stroke', color: 'text-yellow-500', label: `${synced}/${total}` };
    return { icon: 'fa-question-circle', color: 'text-stone-400', label: 'Unknown' };
  };

  const handleSync = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (syncing) return;

    setSyncing(true);
    try {
      await apiRequest(`/images/library/${encodeURIComponent(image.id)}/push`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      onSync?.();
    } catch (err) {
      console.error('Failed to sync image:', err);
      alert(err instanceof Error ? err.message : 'Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const syncStatus = showSyncStatus ? getSyncStatusSummary() : null;

  if (compact) {
    return (
      <div
        draggable={!isPending}
        onDragStart={isPending ? undefined : handleDragStart}
        onDragEnd={isPending ? undefined : handleDragEnd}
        className={`
          group flex items-center gap-2 p-2 rounded-lg border transition-all select-none
          ${isPending ? 'cursor-progress opacity-70 animate-pulse' : 'cursor-grab active:cursor-grabbing'}
          ${isDragging
            ? 'opacity-50 scale-95 border-sage-500 bg-sage-50 dark:bg-sage-900/20'
            : 'glass-surface border-stone-200 dark:border-stone-800 hover:border-stone-300 dark:hover:border-stone-700 hover:shadow-sm'
          }
        `}
      >
        <i className={`${getKindIcon()} ${getKindColor()}`} />
        <span className="flex-1 text-xs text-stone-700 dark:text-stone-200 truncate font-medium">
          {image.filename || image.reference}
        </span>
        {image.version && (
          <span className="text-[10px] text-stone-400">{image.version}</span>
        )}
        <i className="fa-solid fa-grip-vertical text-[10px] text-stone-300 dark:text-stone-600 opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>
    );
  }

  // Build tooltip content
  const tooltipLines = [
    `ID: ${image.id}`,
    `Kind: ${image.kind}`,
    image.vendor && `Vendor: ${image.vendor}`,
    image.size_bytes && `Size: ${formatSize(image.size_bytes)}`,
    image.uploaded_at && `Imported: ${formatDate(image.uploaded_at)}`,
    image.source && `Source: ${image.source}`,
    image.sha256 && `SHA256: ${image.sha256.slice(0, 16)}...`,
    image.notes && `Notes: ${image.notes}`,
  ].filter(Boolean);
  const tooltipText = tooltipLines.join('\n');

  return (
    <div
      draggable={!isPending}
      onDragStart={isPending ? undefined : handleDragStart}
      onDragEnd={isPending ? undefined : handleDragEnd}
      title={tooltipText}
      className={`
        group relative rounded-lg border transition-all duration-200 select-none
        ${isPending ? 'cursor-progress opacity-70 animate-pulse' : 'cursor-grab active:cursor-grabbing'}
        ${isDragging
          ? 'opacity-50 scale-95 border-sage-500 bg-sage-50 dark:bg-sage-900/20'
          : 'glass-surface border-stone-200 dark:border-stone-800 hover:border-stone-300 dark:hover:border-stone-700 hover:shadow-sm'
        }
      `}
    >
      <div className="p-2.5">
        {/* Single row layout */}
        <div className="flex items-center gap-2">
          {/* Icon */}
          <div
            className={`
              w-7 h-7 rounded flex items-center justify-center shrink-0
              ${image.kind === 'docker'
                ? 'bg-blue-100 dark:bg-blue-900/30'
                : image.kind === 'iol'
                ? 'bg-purple-100 dark:bg-purple-900/30'
                : 'bg-orange-100 dark:bg-orange-900/30'
              }
            `}
          >
            <i className={`${getKindIcon()} ${getKindColor()} text-xs`} />
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <h4 className="font-bold text-xs text-stone-900 dark:text-white truncate">
                {image.filename || image.reference}
              </h4>
              {isPending && (
                <span className="px-1 py-0.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 rounded text-[8px] font-bold shrink-0">
                  PROCESSING
                </span>
              )}
              {image.is_default && (
                <span className="px-1 py-0.5 bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 rounded text-[8px] font-bold shrink-0">
                  DEFAULT
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5 text-[10px] text-stone-500">
              <span className="uppercase font-bold">{image.kind}</span>
              {image.sha256 && (
                <>
                  <span className="text-stone-300 dark:text-stone-600">•</span>
                  <span className="text-emerald-600 dark:text-emerald-400" title={`SHA256: ${image.sha256}`}>
                    <i className="fa-solid fa-check-circle text-[8px]" /> verified
                  </span>
                </>
              )}
              {image.version && (
                <>
                  <span className="text-stone-300 dark:text-stone-600">•</span>
                  <span>{image.version}</span>
                </>
              )}
              {image.device_id && (
                <>
                  <span className="text-stone-300 dark:text-stone-600">•</span>
                  <span className="text-sage-600 dark:text-sage-400">{image.device_id}</span>
                </>
              )}
              {image.uploaded_at && (
                <>
                  <span className="text-stone-300 dark:text-stone-600">•</span>
                  <span className="text-stone-400">{formatDate(image.uploaded_at)}</span>
                </>
              )}
              {syncStatus && (
                <>
                  <span className="text-stone-300 dark:text-stone-600">•</span>
                  <span className={syncStatus.color} title={`Agent sync: ${syncStatus.label}`}>
                    <i className={`fa-solid ${syncStatus.icon}`} />
                  </span>
                </>
              )}
              {isPending && (
                <>
                  <span className="text-stone-300 dark:text-stone-600">•</span>
                  <span className="text-amber-600 dark:text-amber-400">
                    <i className="fa-solid fa-circle-notch fa-spin text-[8px] mr-1" />
                    {pendingMessage || 'Processing image...'}
                  </span>
                </>
              )}
            </div>
          </div>

          {/* Actions */}
          {!isPending && (
            <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
              {showSyncStatus && (
                <button
                  onClick={handleSync}
                  disabled={syncing}
                  className="p-1 rounded text-stone-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100/50 dark:hover:bg-blue-800/50 disabled:opacity-50"
                  title="Sync to all agents"
                >
                  <i className={`fa-solid fa-sync text-[10px] ${syncing ? 'fa-spin' : ''}`} />
                </button>
              )}
              {image.device_id && !image.is_default && onSetDefault && (
                <button
                  onClick={(e) => { e.stopPropagation(); onSetDefault(); }}
                  className="p-1 rounded text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 hover:bg-sage-100/50 dark:hover:bg-sage-800/50"
                  title="Set as default"
                >
                  <i className="fa-solid fa-star text-[10px]" />
                </button>
              )}
              {onUnassign && (
                <button
                  onClick={(e) => { e.stopPropagation(); onUnassign(); }}
                  className="p-1 rounded text-stone-400 hover:text-amber-600 hover:bg-amber-100/50 dark:hover:bg-amber-800/50"
                  title="Unassign from device"
                >
                  <i className="fa-solid fa-link-slash text-[10px]" />
                </button>
              )}
              {onDelete && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (window.confirm('Delete this image from the library? This cannot be undone.')) {
                      onDelete();
                    }
                  }}
                  className="p-1 rounded text-stone-400 hover:text-red-500 hover:bg-red-100/50 dark:hover:bg-red-800/50"
                  title="Delete image"
                >
                  <i className="fa-solid fa-trash text-[10px]" />
                </button>
              )}
              <i className="fa-solid fa-grip-vertical text-[10px] text-stone-300 dark:text-stone-600 ml-1" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ImageCard;
