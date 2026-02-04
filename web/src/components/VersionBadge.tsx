import React, { useState, useEffect } from 'react';
import { checkForUpdates, UpdateInfo } from '../api';
import { VersionModal } from './VersionModal';

interface VersionBadgeProps {
  className?: string;
}

export const VersionBadge: React.FC<VersionBadgeProps> = ({ className = '' }) => {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchUpdateInfo = async () => {
      try {
        const info = await checkForUpdates();
        setUpdateInfo(info);
      } catch (error) {
        console.error('Failed to check for updates:', error);
        // Try to get at least the version from build-time constant
        const buildVersion = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : null;
        if (buildVersion) {
          setUpdateInfo({
            current_version: buildVersion,
            update_available: false,
          });
        }
      } finally {
        setIsLoading(false);
      }
    };

    fetchUpdateInfo();

    // Refresh every hour
    const interval = setInterval(fetchUpdateInfo, 60 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  if (isLoading || !updateInfo) {
    return null;
  }

  return (
    <>
      <button
        onClick={() => setIsModalOpen(true)}
        className={`
          flex items-center gap-1.5 px-2 py-1
          text-[10px] font-medium
          text-stone-500 dark:text-stone-300
          hover:text-stone-700 dark:hover:text-stone-100
          bg-stone-100 dark:bg-black/60
          hover:bg-stone-200 dark:hover:bg-black/75
          border border-stone-200/50 dark:border-black/70
          rounded-full
          transition-all cursor-pointer
          ${className}
        `}
        title={updateInfo.update_available ? `Update available: v${updateInfo.latest_version}` : 'Version info'}
      >
        {updateInfo.update_available && (
          <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
        )}
        <span>v{updateInfo.current_version}</span>
      </button>

      <VersionModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        updateInfo={updateInfo}
      />
    </>
  );
};

export default VersionBadge;
