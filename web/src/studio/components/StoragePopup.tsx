import React from 'react';
import DetailPopup from './DetailPopup';
import { getStorageColor } from '../../utils/status';
import { formatStorageSize } from '../../utils/format';

interface PerHostMetrics {
  id: string;
  name: string;
  storage_percent: number;
  storage_used_gb: number;
  storage_total_gb: number;
}

interface StorageTotals {
  used_gb: number;
  total_gb: number;
  percent: number;
}

interface StoragePopupProps {
  isOpen: boolean;
  onClose: () => void;
  perHost: PerHostMetrics[];
  totals: StorageTotals;
}

const StoragePopup: React.FC<StoragePopupProps> = ({ isOpen, onClose, perHost, totals }) => {
  return (
    <DetailPopup isOpen={isOpen} onClose={onClose} title="Storage Usage" width="max-w-xl">
      <div className="space-y-6">
        {/* Total Summary */}
        <div className="bg-stone-100 dark:bg-stone-800/50 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-stone-700 dark:text-stone-300">
              Total Storage
            </span>
            <span className="text-sm text-stone-600 dark:text-stone-400">
              {formatStorageSize(totals.used_gb)} / {formatStorageSize(totals.total_gb)}
            </span>
          </div>
          <div className="h-4 bg-stone-200 dark:bg-stone-700 rounded overflow-hidden">
            <div
              className={`h-full ${getStorageColor(totals.percent)} transition-all`}
              style={{ width: `${Math.min(totals.percent, 100)}%` }}
            />
          </div>
          <div className="mt-1 text-right text-xs text-stone-500 dark:text-stone-400">
            {totals.percent.toFixed(1)}% used
          </div>
        </div>

        {/* Per-Host Breakdown */}
        {perHost.length > 1 && (
          <div>
            <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 mb-3 flex items-center gap-2">
              <i className="fa-solid fa-server text-stone-400" />
              By Host
            </h3>
            <div className="space-y-3">
              {perHost.map(host => (
                <div key={host.id}>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="font-medium text-stone-700 dark:text-stone-300">{host.name}</span>
                    <span className="text-stone-500 dark:text-stone-400">
                      {formatStorageSize(host.storage_used_gb)} / {formatStorageSize(host.storage_total_gb)}
                    </span>
                  </div>
                  <div className="h-4 bg-stone-200 dark:bg-stone-700 rounded overflow-hidden">
                    <div
                      className={`h-full ${getStorageColor(host.storage_percent)} transition-all`}
                      style={{ width: `${Math.min(host.storage_percent, 100)}%` }}
                    />
                  </div>
                  <div className="mt-0.5 text-right text-[10px] text-stone-400">
                    {host.storage_percent.toFixed(1)}%
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Single host info */}
        {perHost.length === 1 && (
          <div className="text-sm text-stone-500 dark:text-stone-400 text-center py-2">
            <i className="fa-solid fa-info-circle mr-1" />
            Single-host environment: {perHost[0].name}
          </div>
        )}

        {/* No hosts */}
        {perHost.length === 0 && (
          <div className="text-sm text-stone-500 dark:text-stone-400 text-center py-4">
            No storage data available
          </div>
        )}

        {/* Legend */}
        <div className="pt-4 border-t border-stone-200 dark:border-stone-700">
          <h4 className="text-xs font-medium text-stone-500 dark:text-stone-400 mb-2">Thresholds</h4>
          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded bg-violet-500" />
              <span className="text-stone-600 dark:text-stone-400">Normal</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded bg-amber-500" />
              <span className="text-stone-600 dark:text-stone-400">75%+</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded bg-red-500" />
              <span className="text-stone-600 dark:text-stone-400">90%+</span>
            </div>
          </div>
        </div>
      </div>
    </DetailPopup>
  );
};

export default StoragePopup;
