import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { apiRequest } from '../api';
import DeviceManager from '../studio/components/DeviceManager';
import { DeviceModel, DeviceType, ImageLibraryEntry } from '../studio/types';

interface DeviceCatalogEntry {
  id: string;
  label: string;
  support?: string;
}

interface CustomDevice {
  id: string;
  label: string;
}

const DEFAULT_ICON = 'fa-microchip';

const guessDeviceType = (id: string, label: string): DeviceType => {
  const token = `${id} ${label}`.toLowerCase();
  if (token.includes('switch')) return DeviceType.SWITCH;
  if (token.includes('router')) return DeviceType.ROUTER;
  if (token.includes('firewall')) return DeviceType.FIREWALL;
  if (token.includes('linux') || token.includes('server') || token.includes('host')) return DeviceType.HOST;
  return DeviceType.CONTAINER;
};

const buildDeviceModels = (devices: DeviceCatalogEntry[], images: ImageLibraryEntry[], customDevices: CustomDevice[]): DeviceModel[] => {
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

  const catalogMap = new Map(devices.map((device) => [device.id, device]));
  const customMap = new Map(customDevices.map((device) => [device.id, device]));
  const deviceIds = new Set<string>(devices.map((device) => device.id));
  imageDeviceIds.forEach((deviceId) => deviceIds.add(deviceId));
  customDevices.forEach((device) => deviceIds.add(device.id));

  return Array.from(deviceIds).map((deviceId) => {
    const device = catalogMap.get(deviceId);
    const custom = customMap.get(deviceId);
    const label = device?.label || custom?.label || deviceId;
    const versions = Array.from(versionsByDevice.get(deviceId) || []);
    return {
      id: deviceId,
      type: guessDeviceType(deviceId, label),
      name: label,
      icon: DEFAULT_ICON,
      versions: versions.length > 0 ? versions : ['default'],
      isActive: true,
      vendor: device?.support || custom?.label ? 'custom' : 'custom',
    };
  });
};

const ImagesPage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user, loading: userLoading } = useUser();
  const navigate = useNavigate();
  const [showThemeSelector, setShowThemeSelector] = useState(false);

  const [deviceCatalog, setDeviceCatalog] = useState<DeviceCatalogEntry[]>([]);
  const [imageLibrary, setImageLibrary] = useState<ImageLibraryEntry[]>([]);
  const [imageCatalog, setImageCatalog] = useState<Record<string, { clab?: string; libvirt?: string; virtualbox?: string; caveats?: string[] }>>({});
  const [customDevices, setCustomDevices] = useState<CustomDevice[]>(() => {
    const stored = localStorage.getItem('aura_custom_devices');
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
      const data = await apiRequest<{ devices?: DeviceCatalogEntry[] }>('/devices');
      setDeviceCatalog(data.devices || []);
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
    localStorage.setItem('aura_custom_devices', JSON.stringify(next));
  };

  const deviceModels = buildDeviceModels(deviceCatalog, imageLibrary, customDevices);

  // Redirect if not authenticated
  if (!userLoading && !user) {
    return <Navigate to="/" replace />;
  }

  return (
    <>
      <div className="min-h-screen bg-stone-50 dark:bg-stone-900 flex flex-col overflow-hidden">
        <header className="h-16 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-sage-600 rounded-xl flex items-center justify-center shadow-lg shadow-sage-900/20 border border-sage-400/30">
              <i className="fa-solid fa-bolt-lightning text-white"></i>
            </div>
            <div>
              <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">AURA</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Image Management</p>
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

        <main className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
              <span className="ml-3 text-stone-500">Loading...</span>
            </div>
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

export default ImagesPage;
