import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate, useLocation } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { apiRequest } from '../api';
import DeviceManager from '../studio/components/DeviceManager';
import DeviceConfigManager from '../studio/components/DeviceConfigManager';
import { DeviceModel, ImageLibraryEntry } from '../studio/types';
import { DeviceCategory } from '../studio/constants';
import { ArchetypeIcon } from '../components/icons';

interface CustomDevice {
  id: string;
  label: string;
}

/**
 * Flatten vendor categories into a flat list of DeviceModels
 */
const flattenVendorCategories = (categories: DeviceCategory[]): DeviceModel[] => {
  return categories.flatMap(cat => {
    if (cat.subCategories) {
      return cat.subCategories.flatMap(sub => sub.models);
    }
    return cat.models || [];
  });
};

/**
 * Build device models by merging vendor registry with image library data
 */
const buildDeviceModels = (
  vendorCategories: DeviceCategory[],
  images: ImageLibraryEntry[],
  customDevices: CustomDevice[]
): DeviceModel[] => {
  // Get all devices from vendor registry
  const vendorDevices = flattenVendorCategories(vendorCategories);
  const vendorDeviceMap = new Map(vendorDevices.map(d => [d.id, d]));

  // Collect versions from image library
  const versionsByDevice = new Map<string, Set<string>>();
  const imageDeviceIds = new Set<string>();
  images.forEach((image) => {
    if (!image.device_id) return;
    imageDeviceIds.add(image.device_id);
    const versions = versionsByDevice.get(image.device_id) || new Set<string>();
    if (image.version) {
      versions.add(image.version);
    }
    versionsByDevice.set(image.device_id, versions);
  });

  // Start with vendor devices (preserves rich metadata like icons, types, vendors)
  const result: DeviceModel[] = vendorDevices.map(device => {
    const imageVersions = Array.from(versionsByDevice.get(device.id) || []);
    return {
      ...device,
      // Merge versions from both vendor registry and image library
      versions: imageVersions.length > 0
        ? [...new Set([...device.versions, ...imageVersions])]
        : device.versions,
    };
  });

  // Add custom devices that aren't in vendor registry
  customDevices.forEach(custom => {
    if (!vendorDeviceMap.has(custom.id)) {
      const imageVersions = Array.from(versionsByDevice.get(custom.id) || []);
      result.push({
        id: custom.id,
        type: 'container' as DeviceModel['type'],
        name: custom.label,
        icon: 'fa-microchip',
        versions: imageVersions.length > 0 ? imageVersions : ['default'],
        isActive: true,
        vendor: 'custom',
        isCustom: true,
      });
    }
  });

  // Add devices that have images but aren't in vendor registry or custom
  imageDeviceIds.forEach(deviceId => {
    if (!vendorDeviceMap.has(deviceId) && !customDevices.find(c => c.id === deviceId)) {
      const imageVersions = Array.from(versionsByDevice.get(deviceId) || []);
      result.push({
        id: deviceId,
        type: 'container' as DeviceModel['type'],
        name: deviceId,
        icon: 'fa-microchip',
        versions: imageVersions.length > 0 ? imageVersions : ['default'],
        isActive: true,
        vendor: 'unknown',
      });
    }
  });

  return result;
};

type TabType = 'devices' | 'images';

const NodesPage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user, loading: userLoading } = useUser();
  const navigate = useNavigate();
  const location = useLocation();
  const [showThemeSelector, setShowThemeSelector] = useState(false);

  // Determine active tab from URL
  const getActiveTab = (): TabType => {
    if (location.pathname === '/nodes/images') return 'images';
    if (location.pathname === '/nodes/devices') return 'devices';
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

  const [vendorCategories, setVendorCategories] = useState<DeviceCategory[]>([]);
  const [imageLibrary, setImageLibrary] = useState<ImageLibraryEntry[]>([]);
  const [imageCatalog, setImageCatalog] = useState<Record<string, { clab?: string; libvirt?: string; virtualbox?: string; caveats?: string[] }>>({});
  const [customDevices, setCustomDevices] = useState<CustomDevice[]>(() => {
    const stored = localStorage.getItem('archetype_custom_devices');
    if (!stored) return [];
    try {
      const parsed = JSON.parse(stored) as CustomDevice[];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  });
  const [loading, setLoading] = useState(true);

  const loadDevices = useCallback(async () => {
    try {
      // Fetch vendor categories (comprehensive device list with rich metadata)
      const vendorData = await apiRequest<DeviceCategory[]>('/vendors');
      setVendorCategories(vendorData || []);
      // Fetch image catalog and library
      const imageData = await apiRequest<{ images?: Record<string, { clab?: string; libvirt?: string; virtualbox?: string; caveats?: string[] }> }>('/images');
      setImageCatalog(imageData.images || {});
      const libraryData = await apiRequest<{ images?: ImageLibraryEntry[] }>('/images/library');
      setImageLibrary(libraryData.images || []);
    } catch (err) {
      console.error('Failed to load devices:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  const updateCustomDevices = (next: CustomDevice[]) => {
    setCustomDevices(next);
    localStorage.setItem('archetype_custom_devices', JSON.stringify(next));
  };

  const deviceModels = buildDeviceModels(vendorCategories, imageLibrary, customDevices);

  // Redirect if not authenticated
  if (!userLoading && !user) {
    return <Navigate to="/" replace />;
  }

  return (
    <>
      <div className="min-h-screen bg-stone-50 dark:bg-stone-900 flex flex-col overflow-hidden">
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
              onClick={loadDevices}
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
          </div>
        </div>

        <main className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
              <span className="ml-3 text-stone-500">Loading...</span>
            </div>
          ) : activeTab === 'devices' ? (
            <DeviceConfigManager
              deviceModels={deviceModels}
              onRefresh={loadDevices}
            />
          ) : (
            <DeviceManager
              deviceModels={deviceModels}
              imageCatalog={imageCatalog}
              imageLibrary={imageLibrary}
              customDevices={customDevices}
              onAddCustomDevice={(device) => updateCustomDevices([...customDevices, device])}
              onRemoveCustomDevice={(deviceId) => updateCustomDevices(customDevices.filter((item) => item.id !== deviceId))}
              onUploadImage={loadDevices}
              onUploadQcow2={loadDevices}
              onRefresh={loadDevices}
            />
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
