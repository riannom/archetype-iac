import React from 'react';
import { DeviceModel, ImageLibraryEntry } from '../types';
import { useDropHandlers } from '../contexts/DragContext';
import { formatSize } from '../../utils/format';

interface DeviceCardProps {
  device: DeviceModel;
  assignedImages: ImageLibraryEntry[];
  breadcrumb?: string;
  isSelected: boolean;
  onSelect: () => void;
  onUnassignImage: (imageId: string, deviceId?: string) => void;
  onSetDefaultImage: (imageId: string) => void;
}

const DeviceCard: React.FC<DeviceCardProps> = ({
  device,
  assignedImages,
  breadcrumb,
  isSelected,
  onSelect,
  onUnassignImage,
  onSetDefaultImage,
}) => {
  const { handleDragOver, handleDragLeave, handleDrop, isDropTarget, isDragging } =
    useDropHandlers(device.id);

  const defaultImage = assignedImages.find((img) => img.is_default);
  const hasImages = assignedImages.length > 0;

  // Get status indicator
  const getStatusIndicator = () => {
    if (defaultImage) {
      return (
        <span
          className="w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-sm ring-2 ring-emerald-500/20"
          title="Has default image"
        />
      );
    }
    if (hasImages) {
      return (
        <span
          className="w-2.5 h-2.5 rounded-full bg-blue-500 shadow-sm ring-2 ring-blue-500/20"
          title="Has images (no default)"
        />
      );
    }
    return (
      <span
        className="w-2.5 h-2.5 rounded-full bg-amber-500 shadow-sm ring-2 ring-amber-500/20"
        title="No images assigned"
      />
    );
  };

  return (
    <div
      onClick={onSelect}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`
        relative rounded-xl border transition-all duration-200 cursor-pointer
        ${isSelected
          ? 'glass-surface border-stone-200 dark:border-stone-800 border-l-2 border-l-sage-500 dark:border-l-sage-400'
          : 'glass-surface border-stone-200 dark:border-stone-800 hover:border-stone-300 dark:hover:border-stone-700'
        }
        ${isDropTarget
          ? 'border-sage-500 border-dashed border-2 bg-sage-50/50 dark:bg-sage-900/30 scale-[1.02] shadow-lg shadow-sage-500/20'
          : ''
        }
        ${isDragging && !isDropTarget ? 'opacity-70' : ''}
      `}
    >
      {/* Header */}
      <div className="p-4">
        <div className="flex items-start gap-3">
          {/* Icon */}
          <div
            className={`
              w-12 h-12 rounded-lg flex items-center justify-center text-lg shrink-0
              ${isSelected
                ? 'bg-sage-100 dark:bg-stone-800 text-sage-600 dark:text-sage-400'
                : 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400'
              }
            `}
          >
            <i className={`fa-solid ${device.icon}`}></i>
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              {getStatusIndicator()}
              <h3 className="font-bold text-sm text-stone-900 dark:text-white truncate">
                {device.name}
              </h3>
            </div>
            {breadcrumb && (
              <p className="text-[10px] text-stone-400 dark:text-stone-500 truncate mt-0.5">
                {breadcrumb}
              </p>
            )}
            <div className="flex flex-col gap-0.5 mt-1">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold text-stone-500 dark:text-stone-400 uppercase">
                  {device.vendor}
                </span>
                {device.licenseRequired && (
                  <span className="text-[9px] px-1.5 py-0.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 rounded font-bold">
                    License
                  </span>
                )}
              </div>
              <span className="text-[10px] text-stone-400 dark:text-stone-500 font-mono">
                {device.id}
              </span>
            </div>
          </div>

          {/* Image count badge */}
          <div
            className={`
              px-2 py-1 rounded-md text-[10px] font-bold
              ${hasImages
                ? 'bg-sage-100 dark:bg-sage-800 text-sage-700 dark:text-sage-400'
                : 'bg-stone-100 dark:bg-stone-800 text-stone-500'
              }
            `}
          >
            {assignedImages.length}
          </div>
        </div>
      </div>

      {/* Drop Zone (visible when dragging) */}
      {isDragging && (
        <div
          className={`
            mx-4 mb-3 py-3 border-2 border-dashed rounded-lg text-center transition-all
            ${isDropTarget
              ? 'border-sage-500 bg-sage-100/50 dark:bg-sage-900/30'
              : 'border-stone-300 dark:border-stone-700'
            }
          `}
        >
          <i className={`fa-solid fa-arrow-down text-sm ${isDropTarget ? 'text-sage-500' : 'text-stone-400'}`} />
          <p className={`text-[10px] font-bold mt-1 ${isDropTarget ? 'text-sage-600 dark:text-sage-400' : 'text-stone-400'}`}>
            Drop image here
          </p>
        </div>
      )}

      {/* Assigned Images (collapsed view) */}
      {!isDragging && assignedImages.length > 0 && (
        <div className="px-4 pb-4 space-y-2">
          {assignedImages.slice(0, 3).map((img) => (
            <div
              key={img.id}
              className={`
                flex items-center gap-2 p-2 rounded-lg text-[11px]
                ${img.is_default
                  ? 'bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800'
                  : 'bg-stone-50 dark:bg-stone-800/50 border border-stone-200 dark:border-stone-700'
                }
              `}
            >
              <i
                className={`fa-solid ${img.kind === 'docker' ? 'fa-docker text-blue-500' : 'fa-hard-drive text-orange-500'}`}
              />
              <div className="flex-1 min-w-0">
                <span className="font-medium text-stone-700 dark:text-stone-200 truncate block">
                  {img.filename || img.reference}
                </span>
                <span className="text-[9px] text-stone-400">
                  {img.version && <span>{img.version}</span>}
                  {img.size_bytes && <span className="ml-2">{formatSize(img.size_bytes)}</span>}
                </span>
              </div>
              {img.is_default && (
                <span className="px-1.5 py-0.5 bg-emerald-500 text-white text-[9px] font-bold rounded">
                  DEFAULT
                </span>
              )}
              <div className="flex items-center gap-1">
                {!img.is_default && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onSetDefaultImage(img.id);
                    }}
                    className="p-1 hover:bg-stone-200 dark:hover:bg-stone-700 rounded text-stone-400 hover:text-sage-600"
                    title="Set as default"
                  >
                    <i className="fa-solid fa-star text-[10px]" />
                  </button>
                )}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onUnassignImage(img.id, device.id);
                  }}
                  className="p-1 hover:bg-red-100 dark:hover:bg-red-900/30 rounded text-stone-400 hover:text-red-500"
                  title="Unassign image"
                >
                  <i className="fa-solid fa-xmark text-[10px]" />
                </button>
              </div>
            </div>
          ))}
          {assignedImages.length > 3 && (
            <p className="text-[10px] text-stone-400 text-center">
              +{assignedImages.length - 3} more
            </p>
          )}
        </div>
      )}

      {/* Tags (if device has tags) */}
      {device.tags && device.tags.length > 0 && (
        <div className="px-4 pb-3 flex flex-wrap gap-1">
          {device.tags.slice(0, 4).map((tag) => (
            <span
              key={tag}
              className="px-1.5 py-0.5 bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400 text-[9px] rounded"
            >
              {tag}
            </span>
          ))}
          {device.tags.length > 4 && (
            <span className="text-[9px] text-stone-400">+{device.tags.length - 4}</span>
          )}
        </div>
      )}
    </div>
  );
};

export default DeviceCard;
