import React from 'react';
import { ScanResponse, formatBytes } from './types';

interface ISOReviewStepProps {
  scanResult: ScanResponse;
  selectedImages: Set<string>;
  toggleImage: (imageId: string) => void;
  selectAll: () => void;
  selectNone: () => void;
  createDevices: boolean;
  setCreateDevices: (value: boolean) => void;
  error: string | null;
}

export const ISOReviewStep: React.FC<ISOReviewStepProps> = ({
  scanResult,
  selectedImages,
  toggleImage,
  selectAll,
  selectNone,
  createDevices,
  setCreateDevices,
  error,
}) => {
  return (
    <div className="space-y-6">
      {/* ISO Info */}
      <div className="bg-stone-50 dark:bg-stone-800/50 rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div>
            <h4 className="text-xs font-bold text-stone-600 dark:text-stone-300">
              <i className="fa-solid fa-compact-disc mr-2 text-sage-500" />
              {scanResult.iso_path.split('/').pop()}
            </h4>
            <p className="text-[11px] text-stone-400 mt-1">
              Format: {scanResult.format.toUpperCase()} | Size: {formatBytes(scanResult.size_bytes)}
            </p>
          </div>
          <div className="text-right">
            <div className="text-lg font-bold text-stone-700 dark:text-stone-300">
              {scanResult.images.length}
            </div>
            <div className="text-[11px] text-stone-400 uppercase">Images Found</div>
          </div>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
          <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* Node Definitions */}
      {scanResult.node_definitions.length > 0 && (
        <div>
          <h4 className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-wider mb-3">
            Device Types ({scanResult.node_definitions.length})
          </h4>
          <div className="grid grid-cols-2 gap-2">
            {scanResult.node_definitions.map((nd) => (
              <div
                key={nd.id}
                className="p-3 bg-white dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg"
              >
                <div className="flex items-center gap-2">
                  <i
                    className={`fa-solid ${
                      nd.nature === 'firewall'
                        ? 'fa-shield-halved text-red-500'
                        : nd.nature === 'router'
                        ? 'fa-arrows-to-dot text-blue-500'
                        : 'fa-server text-stone-500'
                    }`}
                  />
                  <span className="text-xs font-bold text-stone-700 dark:text-stone-300">{nd.label}</span>
                </div>
                <p className="text-[11px] text-stone-400 mt-1">
                  {nd.ram_mb}MB RAM | {nd.cpus} vCPUs | {nd.interfaces.length} interfaces
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Images Selection */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-wider">
            Images to Import ({selectedImages.size} / {scanResult.images.length})
          </h4>
          <div className="flex gap-2">
            <button
              onClick={selectAll}
              className="text-[11px] text-sage-600 dark:text-sage-400 hover:underline font-bold"
            >
              Select All
            </button>
            <button
              onClick={selectNone}
              className="text-[11px] text-stone-500 hover:underline font-bold"
            >
              Select None
            </button>
          </div>
        </div>
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {scanResult.images.map((img) => {
            const nd = scanResult.node_definitions.find((n) => n.id === img.node_definition_id);
            return (
              <label
                key={img.id}
                className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all ${
                  selectedImages.has(img.id)
                    ? 'bg-sage-50 dark:bg-stone-800 border-sage-300 dark:border-sage-600'
                    : 'bg-white dark:bg-stone-800 border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedImages.has(img.id)}
                  onChange={() => toggleImage(img.id)}
                  className="w-4 h-4 rounded border-stone-300 text-sage-600 focus:ring-sage-500"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-stone-700 dark:text-stone-300 truncate">
                      {img.label || img.id}
                    </span>
                    <span
                      className={`px-1.5 py-0.5 text-[11px] font-bold rounded ${
                        img.image_type === 'qcow2'
                          ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400'
                          : 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                      }`}
                    >
                      {img.image_type.toUpperCase()}
                    </span>
                  </div>
                  <p className="text-[11px] text-stone-400 truncate">
                    {nd?.label || img.node_definition_id} | {img.version || 'unknown version'} |{' '}
                    {formatBytes(img.size_bytes)}
                  </p>
                </div>
              </label>
            );
          })}
        </div>
      </div>

      {/* Options */}
      <div className="border-t border-stone-200 dark:border-stone-800 pt-4">
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={createDevices}
            onChange={(e) => setCreateDevices(e.target.checked)}
            className="w-4 h-4 rounded border-stone-300 text-sage-600 focus:ring-sage-500"
          />
          <div>
            <span className="text-xs font-bold text-stone-700 dark:text-stone-300">
              Create device types for new definitions
            </span>
            <p className="text-[11px] text-stone-400">
              Automatically create custom device types for node definitions not in the vendor registry
            </p>
          </div>
        </label>
      </div>

      {scanResult.parse_errors.length > 0 && (
        <div className="p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
          <h5 className="text-xs font-bold text-amber-600 dark:text-amber-400 mb-1">
            <i className="fa-solid fa-triangle-exclamation mr-2" />
            Parse Warnings
          </h5>
          <ul className="text-[11px] text-amber-600 dark:text-amber-400 space-y-0.5">
            {scanResult.parse_errors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
