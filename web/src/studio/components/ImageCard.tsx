import React from 'react';
import { ImageLibraryEntry } from '../types';
import { useDragHandlers, useDragContext } from '../contexts/DragContext';
import { formatSize, formatDate } from '../../utils/format';

interface ImageCardProps {
  image: ImageLibraryEntry;
  onUnassign?: () => void;
  onSetDefault?: () => void;
  onDelete?: () => void;
  compact?: boolean;
}

const ImageCard: React.FC<ImageCardProps> = ({
  image,
  onUnassign,
  onSetDefault,
  onDelete,
  compact = false,
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

  const isDragging = dragState.draggedImageId === image.id;

  const getKindIcon = () => {
    if (image.kind === 'docker') {
      return 'fa-docker';
    }
    return 'fa-hard-drive';
  };

  const getKindColor = () => {
    if (image.kind === 'docker') {
      return 'text-blue-500';
    }
    return 'text-orange-500';
  };

  if (compact) {
    return (
      <div
        draggable
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        className={`
          group flex items-center gap-2 p-2 rounded-lg border transition-all cursor-grab active:cursor-grabbing
          ${isDragging
            ? 'opacity-50 scale-95 border-sage-500 bg-sage-50 dark:bg-sage-900/20'
            : 'bg-white dark:bg-stone-900 border-stone-200 dark:border-stone-800 hover:border-stone-300 dark:hover:border-stone-700 hover:shadow-sm'
          }
        `}
      >
        <i className={`fa-brands ${getKindIcon()} ${getKindColor()}`} />
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

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      className={`
        group relative rounded-xl border transition-all duration-200 cursor-grab active:cursor-grabbing
        ${isDragging
          ? 'opacity-50 scale-95 border-sage-500 bg-sage-50 dark:bg-sage-900/20 rotate-1'
          : 'bg-white dark:bg-stone-900 border-stone-200 dark:border-stone-800 hover:border-stone-300 dark:hover:border-stone-700 hover:shadow-md'
        }
      `}
    >
      {/* Drag handle indicator */}
      <div className="absolute top-0 left-0 right-0 h-1.5 bg-stone-100 dark:bg-stone-800 rounded-t-xl opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
        <div className="w-8 h-1 bg-stone-300 dark:bg-stone-600 rounded-full" />
      </div>

      <div className="p-4 pt-5">
        {/* Header row */}
        <div className="flex items-start gap-3">
          {/* Icon */}
          <div
            className={`
              w-10 h-10 rounded-lg flex items-center justify-center shrink-0
              ${image.kind === 'docker'
                ? 'bg-blue-100 dark:bg-blue-900/30'
                : 'bg-orange-100 dark:bg-orange-900/30'
              }
            `}
          >
            <i className={`fa-brands ${getKindIcon()} ${getKindColor()} text-lg`} />
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <h4 className="font-bold text-sm text-stone-900 dark:text-white truncate">
              {image.filename || image.reference}
            </h4>
            <div className="flex items-center gap-2 mt-1 text-[10px] text-stone-500">
              <span className="uppercase font-bold">{image.kind}</span>
              {image.size_bytes && (
                <>
                  <span className="text-stone-300 dark:text-stone-600">|</span>
                  <span>{formatSize(image.size_bytes)}</span>
                </>
              )}
              {image.vendor && (
                <>
                  <span className="text-stone-300 dark:text-stone-600">|</span>
                  <span>{image.vendor}</span>
                </>
              )}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {image.device_id && !image.is_default && onSetDefault && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onSetDefault();
                }}
                className="p-1.5 hover:bg-sage-100 dark:hover:bg-sage-900/30 rounded text-stone-400 hover:text-sage-600 dark:hover:text-sage-400"
                title="Set as default"
              >
                <i className="fa-solid fa-star text-xs" />
              </button>
            )}
            {onUnassign && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onUnassign();
                }}
                className="p-1.5 hover:bg-amber-100 dark:hover:bg-amber-900/30 rounded text-stone-400 hover:text-amber-600"
                title="Unassign from device"
              >
                <i className="fa-solid fa-link-slash text-xs" />
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
                className="p-1.5 hover:bg-red-100 dark:hover:bg-red-900/30 rounded text-stone-400 hover:text-red-500"
                title="Delete image"
              >
                <i className="fa-solid fa-trash text-xs" />
              </button>
            )}
          </div>
        </div>

        {/* Metadata row */}
        <div className="mt-3 flex items-center gap-3 text-[10px]">
          {image.version && (
            <div className="flex items-center gap-1">
              <i className="fa-solid fa-tag text-stone-400" />
              <span className="text-stone-600 dark:text-stone-400">{image.version}</span>
            </div>
          )}
          {image.uploaded_at && (
            <div className="flex items-center gap-1">
              <i className="fa-solid fa-calendar text-stone-400" />
              <span className="text-stone-600 dark:text-stone-400">{formatDate(image.uploaded_at)}</span>
            </div>
          )}
          {image.is_default && (
            <span className="px-1.5 py-0.5 bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 rounded font-bold">
              DEFAULT
            </span>
          )}
        </div>

        {/* Current assignment */}
        {image.device_id && (
          <div className="mt-3 pt-3 border-t border-stone-100 dark:border-stone-800">
            <div className="flex items-center gap-2 text-[10px]">
              <i className="fa-solid fa-link text-stone-400" />
              <span className="text-stone-500">Assigned to</span>
              <span className="font-bold text-stone-700 dark:text-stone-300">{image.device_id}</span>
            </div>
          </div>
        )}

        {/* Notes */}
        {image.notes && (
          <div className="mt-2">
            <p className="text-[10px] text-stone-400 italic truncate">{image.notes}</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default ImageCard;
