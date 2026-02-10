import React, { useEffect, useState } from 'react';
import { Navigate, useNavigate, useLocation } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { canManageImages } from '../utils/permissions';
import { useImageLibrary } from '../contexts/ImageLibraryContext';
import { useDeviceCatalog } from '../contexts/DeviceCatalogContext';
import DeviceManager from '../studio/components/DeviceManager';
import DeviceConfigManager from '../studio/components/DeviceConfigManager';
import ImageSyncProgress from '../components/ImageSyncProgress';
import { ArchetypeIcon } from '../components/icons';

type TabType = 'devices' | 'images' | 'sync';

const NodesPage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user, loading: userLoading } = useUser();
  const { imageLibrary } = useImageLibrary();
  const {
    deviceModels,
    imageCatalog,
    addCustomDevice,
    removeCustomDevice,
    loading: catalogLoading,
    refresh: refreshCatalog,
  } = useDeviceCatalog();
  const navigate = useNavigate();
  const location = useLocation();
  const [showThemeSelector, setShowThemeSelector] = useState(false);

  // Determine active tab from URL
  const getActiveTab = (): TabType => {
    if (location.pathname === '/nodes/images') return 'images';
    if (location.pathname === '/nodes/devices') return 'devices';
    if (location.pathname === '/nodes/sync') return 'sync';
    return 'devices'; // Default to devices tab
  };

  const [activeTab, setActiveTab] = useState<TabType>(getActiveTab);

  // Sync tab with URL
  useEffect(() => {
    setActiveTab(getActiveTab());
  }, [location.pathname]);

  const handleTabChange = (tab: TabType) => {
    setActiveTab(tab);
    navigate(`/nodes/${tab}`);
  };

  // Build list of custom device IDs from deviceModels (those with vendor='custom' or isCustom=true)
  const customDevices = deviceModels
    .filter(d => d.vendor === 'custom' || d.isCustom)
    .map(d => ({ id: d.id, label: d.name }));

  // Wrapper for adding custom device (adapts the context's API)
  const handleAddCustomDevice = async (device: { id: string; label: string }) => {
    await addCustomDevice({
      id: device.id,
      name: device.label,
      type: 'container',
      vendor: 'Custom',
    });
  };

  // Wrapper for removing custom device
  const handleRemoveCustomDevice = async (deviceId: string) => {
    await removeCustomDevice(deviceId);
  };

  // Redirect if not authenticated or insufficient role
  if (!userLoading && (!user || !canManageImages(user))) {
    return <Navigate to="/" replace />;
  }

  return (
    <>
      <div className="h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] flex flex-col overflow-hidden">
        <header className="h-16 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10 shrink-0">
          <div className="flex items-center gap-4">
            <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
            <div>
              <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Node Management</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            >
              <i className="fa-solid fa-arrow-left text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Back</span>
            </button>

            <button
              onClick={() => setShowThemeSelector(true)}
              className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
              title="Theme Settings"
            >
              <i className="fa-solid fa-palette text-sm"></i>
            </button>

            <button
              onClick={toggleMode}
              className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
              title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
            >
              <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
            </button>

            <button
              onClick={refreshCatalog}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            >
              <i className="fa-solid fa-rotate text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Refresh</span>
            </button>
          </div>
        </header>

        {/* Tab Bar */}
        <div className="border-b border-stone-200 dark:border-stone-800 bg-white/50 dark:bg-stone-900/50 px-10">
          <div className="flex gap-1">
            <button
              onClick={() => handleTabChange('devices')}
              className={`px-6 py-3 text-xs font-bold uppercase tracking-wider transition-all border-b-2 -mb-px ${
                activeTab === 'devices'
                  ? 'text-sage-600 dark:text-sage-400 border-sage-600 dark:border-sage-400'
                  : 'text-stone-500 dark:text-stone-400 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
              }`}
            >
              <i className="fa-solid fa-sliders mr-2"></i>
              Device Management
            </button>
            <button
              onClick={() => handleTabChange('images')}
              className={`px-6 py-3 text-xs font-bold uppercase tracking-wider transition-all border-b-2 -mb-px ${
                activeTab === 'images'
                  ? 'text-sage-600 dark:text-sage-400 border-sage-600 dark:border-sage-400'
                  : 'text-stone-500 dark:text-stone-400 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
              }`}
            >
              <i className="fa-solid fa-hard-drive mr-2"></i>
              Image Management
            </button>
            <button
              onClick={() => handleTabChange('sync')}
              className={`px-6 py-3 text-xs font-bold uppercase tracking-wider transition-all border-b-2 -mb-px ${
                activeTab === 'sync'
                  ? 'text-sage-600 dark:text-sage-400 border-sage-600 dark:border-sage-400'
                  : 'text-stone-500 dark:text-stone-400 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
              }`}
            >
              <i className="fa-solid fa-sync mr-2"></i>
              Sync Jobs
            </button>
          </div>
        </div>

        <main className="flex-1 overflow-hidden">
          {catalogLoading ? (
            <div className="flex items-center justify-center h-full">
              <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
              <span className="ml-3 text-stone-500">Loading...</span>
            </div>
          ) : activeTab === 'devices' ? (
            <DeviceConfigManager
              deviceModels={deviceModels}
              customDevices={customDevices}
              imageLibrary={imageLibrary}
              onAddCustomDevice={handleAddCustomDevice}
              onRemoveCustomDevice={handleRemoveCustomDevice}
              onRefresh={refreshCatalog}
            />
          ) : activeTab === 'images' ? (
            <DeviceManager
              deviceModels={deviceModels}
              imageCatalog={imageCatalog}
              imageLibrary={imageLibrary}
              onUploadImage={refreshCatalog}
              onUploadQcow2={refreshCatalog}
              onRefresh={refreshCatalog}
            />
          ) : (
            <div className="h-full overflow-auto p-6">
              <div className="max-w-4xl mx-auto">
                <div className="mb-6">
                  <h2 className="text-lg font-bold text-stone-900 dark:text-white">Image Sync Jobs</h2>
                  <p className="text-xs text-stone-500 dark:text-stone-400 mt-1">
                    Track image synchronization progress across agents
                  </p>
                </div>
                <ImageSyncProgress
                  showCompleted={true}
                  maxJobs={20}
                  onJobComplete={refreshCatalog}
                />
              </div>
            </div>
          )}
        </main>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />
    </>
  );
};

export default NodesPage;
