/**
 * Device Catalog Context
 *
 * Provides centralized access to device catalog data across the application.
 * Consolidates vendor registry, image library, and custom devices into a
 * single source of truth.
 */

import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from 'react';
import { apiRequest } from '../api';
import { DeviceModel } from '../studio/types';
import { DeviceCategory } from '../studio/constants';
import { useImageLibrary } from './ImageLibraryContext';
import { buildDeviceModels, enrichDeviceCategories } from '../utils/deviceModels';
import { initializePatterns, setRuntimeAliases } from '../studio/utils/interfaceRegistry';

interface CustomDevicePayload {
  id: string;
  name: string;
  type?: string;
  category?: string;
  vendor?: string;
  icon?: string;
  versions?: string[];
  memory?: number;
  cpu?: number;
  maxPorts?: number;
  portNaming?: string;
  portStartIndex?: number;
  managementInterface?: string;
  requiresImage?: boolean;
  supportedImageKinds?: string[];
  licenseRequired?: boolean;
  documentationUrl?: string;
  tags?: string[];
}

interface VendorIdentityMapResponse {
  interface_aliases?: Record<string, string>;
}

interface DeviceCatalogContextType {
  // Raw data from API
  vendorCategories: DeviceCategory[];

  // Computed/enriched data
  deviceModels: DeviceModel[];
  deviceCategories: DeviceCategory[];

  // Custom device operations (via API)
  addCustomDevice: (device: CustomDevicePayload) => Promise<void>;
  removeCustomDevice: (deviceId: string) => Promise<void>;

  // State
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const DeviceCatalogContext = createContext<DeviceCatalogContextType | null>(null);

interface DeviceCatalogProviderProps {
  children: React.ReactNode;
}

export function DeviceCatalogProvider({ children }: DeviceCatalogProviderProps) {
  const { imageLibrary, refreshImageLibrary } = useImageLibrary();

  const [vendorCategories, setVendorCategories] = useState<DeviceCategory[]>([]);
  const [interfaceAliasMap, setInterfaceAliasMap] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const [vendorData, identityMap] = await Promise.all([
        apiRequest<DeviceCategory[]>('/vendors'),
        apiRequest<VendorIdentityMapResponse>('/vendors/identity-map').catch((err) => {
          console.warn('Failed to fetch vendor identity map, using model-derived aliases:', err);
          return { interface_aliases: {} };
        }),
      ]);
      setVendorCategories(vendorData || []);
      setInterfaceAliasMap(identityMap.interface_aliases || {});

      // Also refresh image library to ensure consistency
      await refreshImageLibrary();
    } catch (err) {
      console.error('Failed to fetch device catalog:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch device catalog');
    } finally {
      setLoading(false);
    }
  }, [refreshImageLibrary]);

  // Fetch on mount
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Refetch when auth token changes (e.g., after login)
  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'token' && e.newValue) {
        fetchData();
      }
    };
    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, [fetchData]);

  // Compute deviceModels from vendor categories and image library
  const deviceModels = useMemo(
    () => buildDeviceModels(vendorCategories, imageLibrary),
    [vendorCategories, imageLibrary]
  );

  // Initialize interface patterns when device models change
  // This ensures the interface registry uses data from /vendors API (single source of truth)
  useEffect(() => {
    initializePatterns(deviceModels);
    setRuntimeAliases(interfaceAliasMap);
  }, [deviceModels, interfaceAliasMap]);

  // Compute enriched device categories
  const deviceCategories = useMemo(
    () => enrichDeviceCategories(vendorCategories, deviceModels),
    [vendorCategories, deviceModels]
  );

  // Add a custom device via API
  const addCustomDevice = useCallback(async (device: CustomDevicePayload) => {
    await apiRequest('/vendors', {
      method: 'POST',
      body: JSON.stringify(device),
    });
    // Refresh to get updated vendor list
    await fetchData();
  }, [fetchData]);

  // Remove a custom device via API
  const removeCustomDevice = useCallback(async (deviceId: string) => {
    await apiRequest(`/vendors/${encodeURIComponent(deviceId)}`, {
      method: 'DELETE',
    });
    // Refresh to get updated vendor list
    await fetchData();
  }, [fetchData]);

  const contextValue: DeviceCatalogContextType = useMemo(() => ({
    vendorCategories,
    deviceModels,
    deviceCategories,
    addCustomDevice,
    removeCustomDevice,
    loading,
    error,
    refresh: fetchData,
  }), [
    vendorCategories,
    deviceModels,
    deviceCategories,
    addCustomDevice,
    removeCustomDevice,
    loading,
    error,
    fetchData,
  ]);

  return (
    <DeviceCatalogContext.Provider value={contextValue}>
      {children}
    </DeviceCatalogContext.Provider>
  );
}

/**
 * Hook to access device catalog context
 */
export function useDeviceCatalog(): DeviceCatalogContextType {
  const context = useContext(DeviceCatalogContext);
  if (!context) {
    throw new Error('useDeviceCatalog must be used within a DeviceCatalogProvider');
  }
  return context;
}
