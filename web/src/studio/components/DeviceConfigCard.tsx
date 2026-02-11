import React from 'react';
import { DeviceModel } from '../types';

interface DeviceConfigCardProps {
  device: DeviceModel;
  isSelected: boolean;
  onSelect: () => void;
  isCustom?: boolean;
  isRecentlyAdded?: boolean;
}

const DeviceConfigCard: React.FC<DeviceConfigCardProps> = ({
  device,
  isSelected,
  onSelect,
  isCustom = false,
  isRecentlyAdded = false,
}) => {
  // Format memory display
  const formatMemory = (mb?: number): string => {
    if (!mb) return '-';
    if (mb >= 1024) return `${(mb / 1024).toFixed(1)}GB`;
    return `${mb}MB`;
  };

  // Determine styling based on custom, selected, and recently added state
  const getCardClasses = () => {
    // Recently added - temporary rose highlight with animation
    if (isRecentlyAdded) {
      return 'bg-rose-50 dark:bg-rose-900/30 border-rose-400 dark:border-rose-600 shadow-md animate-pulse';
    }
    if (isSelected) {
      // Subtle left accent bar, keep dark background readable
      return 'glass-surface border-stone-200 dark:border-stone-800 border-l-2 border-l-sage-500 dark:border-l-sage-400';
    }
    if (isCustom) {
      // Subtle differentiation for persistent custom devices
      return 'glass-surface border-stone-300 dark:border-stone-700 hover:border-stone-400 dark:hover:border-stone-600';
    }
    return 'glass-surface border-stone-200 dark:border-stone-800 hover:border-stone-300 dark:hover:border-stone-700';
  };

  // Determine icon background styling
  const getIconClasses = () => {
    if (isRecentlyAdded) {
      return 'bg-rose-600 text-white';
    }
    if (isSelected) {
      return 'bg-sage-100 dark:bg-stone-800 text-sage-600 dark:text-sage-400';
    }
    if (isCustom) {
      return 'bg-stone-200 dark:bg-stone-700 text-stone-600 dark:text-stone-300';
    }
    return 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400';
  };

  // Determine title color
  const getTitleClasses = () => {
    if (isRecentlyAdded) {
      return 'text-rose-700 dark:text-rose-400';
    }
    if (isSelected) {
      return 'text-stone-900 dark:text-white';
    }
    return 'text-stone-900 dark:text-white';
  };

  return (
    <div
      onClick={onSelect}
      className={`p-3 rounded-lg border cursor-pointer transition-all ${getCardClasses()}`}
    >
      <div className="flex items-start gap-3">
        {/* Device icon */}
        <div
          className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${getIconClasses()}`}
        >
          <i className={`fa-solid ${device.icon || 'fa-microchip'} text-sm`}></i>
        </div>

        {/* Device info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className={`text-sm font-bold truncate ${getTitleClasses()}`}>
              {device.name}
            </h3>
            {(device.isCustom || isCustom) && (
              <span className="px-1.5 py-0.5 text-[8px] font-bold uppercase bg-stone-200 dark:bg-stone-700 text-stone-600 dark:text-stone-400 rounded">
                Custom
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] text-stone-500 dark:text-stone-400 font-medium">
              {device.vendor}
            </span>
            <span className="w-1 h-1 rounded-full bg-stone-300 dark:bg-stone-600"></span>
            <span className="text-[10px] text-stone-400 dark:text-stone-500 font-mono">
              {device.id}
            </span>
            <span className="w-1 h-1 rounded-full bg-stone-300 dark:bg-stone-600"></span>
            <span className="text-[10px] text-stone-400 dark:text-stone-500 capitalize">
              {device.type}
            </span>
          </div>

          {/* Resource summary */}
          <div className="flex items-center gap-3 mt-2">
            <div className="flex items-center gap-1 text-[10px] text-stone-400 dark:text-stone-500">
              <i className="fa-solid fa-memory text-[8px]"></i>
              <span>{formatMemory(device.memory)}</span>
            </div>
            <div className="flex items-center gap-1 text-[10px] text-stone-400 dark:text-stone-500">
              <i className="fa-solid fa-microchip text-[8px]"></i>
              <span>{device.cpu || 1} CPU</span>
            </div>
            <div className="flex items-center gap-1 text-[10px] text-stone-400 dark:text-stone-500">
              <i className="fa-solid fa-ethernet text-[8px]"></i>
              <span>{device.maxPorts || 8} ports</span>
            </div>
          </div>
        </div>

        {/* Badges */}
        <div className="flex flex-col items-end gap-1 shrink-0">
          {device.licenseRequired && (
            <span className="px-1.5 py-0.5 text-[8px] font-bold uppercase bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 rounded">
              License
            </span>
          )}
          {!device.isActive && (
            <span className="px-1.5 py-0.5 text-[8px] font-bold uppercase bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400 rounded">
              Inactive
            </span>
          )}
        </div>
      </div>
    </div>
  );
};

export default DeviceConfigCard;
