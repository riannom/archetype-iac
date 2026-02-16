/**
 * Device Model Utilities
 *
 * Shared functions for building and transforming device model data.
 * Used by DeviceCatalogContext to provide consistent device data across the app.
 */

import { DeviceModel, ImageLibraryEntry } from '../studio/types';
import { DeviceCategory } from '../studio/constants';

const INSTANTIABLE_IMAGE_KINDS = new Set(['docker', 'qcow2']);

function normalizeImageKind(kind?: string | null): string {
  return (kind || '').toLowerCase();
}

export function isInstantiableImageKind(kind?: string | null): boolean {
  return INSTANTIABLE_IMAGE_KINDS.has(normalizeImageKind(kind));
}

/**
 * Get all device IDs an image is compatible with.
 * Uses compatible_devices when available, falls back to device_id.
 */
export function getImageDeviceIds(image: ImageLibraryEntry): string[] {
  if (image.compatible_devices?.length) return image.compatible_devices;
  return image.device_id ? [image.device_id] : [];
}

/**
 * Flatten vendor categories into a flat list of DeviceModels
 */
export function flattenVendorCategories(categories: DeviceCategory[]): DeviceModel[] {
  return categories.flatMap(cat => {
    if (cat.subCategories) {
      return cat.subCategories.flatMap(sub => sub.models);
    }
    return cat.models || [];
  });
}

/**
 * Build device models by merging vendor registry with image library data.
 *
 * This function:
 * 1. Starts with vendor devices (preserves rich metadata)
 * 2. Adds version info from assigned images
 * 3. Creates entries for images assigned to unknown device IDs
 *
 * Note: Custom devices are now included in vendorCategories from the API,
 * so no separate customDevices parameter is needed.
 */
export function buildDeviceModels(
  vendorCategories: DeviceCategory[],
  images: ImageLibraryEntry[]
): DeviceModel[] {
  // Get all devices from vendor registry (includes custom devices from API)
  const vendorDevices = flattenVendorCategories(vendorCategories);
  const vendorDeviceMap = new Map(vendorDevices.map(d => [d.id, d]));

  // Collect versions from image library (uses compatible_devices for shared images)
  const versionsByDevice = new Map<string, Set<string>>();
  const imageDeviceIds = new Set<string>();
  images.forEach((image) => {
    if (!isInstantiableImageKind(image.kind)) {
      return;
    }
    getImageDeviceIds(image).forEach((devId) => {
      imageDeviceIds.add(devId);
      const versions = versionsByDevice.get(devId) || new Set<string>();
      if (image.version) {
        versions.add(image.version);
      }
      versionsByDevice.set(devId, versions);
    });
  });

  // Start with vendor devices, merging in image versions
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

  // Add devices that have images but aren't in vendor registry
  imageDeviceIds.forEach(deviceId => {
    if (!vendorDeviceMap.has(deviceId)) {
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
}

/**
 * Enrich vendor categories with merged device models.
 *
 * This ensures the category structure uses the enriched device models
 * (with image version data) rather than the raw vendor data.
 * Also adds an "Other" category for devices discovered from images
 * that aren't in the vendor registry.
 */
export function enrichDeviceCategories(
  vendorCategories: DeviceCategory[],
  deviceModels: DeviceModel[]
): DeviceCategory[] {
  if (vendorCategories.length === 0) {
    return [{ name: 'Devices', models: deviceModels }];
  }

  const deviceMap = new Map(deviceModels.map(d => [d.id, d]));
  const usedDeviceIds = new Set<string>();

  const enrichedCategories = vendorCategories.map(cat => {
    if (cat.subCategories) {
      return {
        ...cat,
        subCategories: cat.subCategories.map(sub => ({
          ...sub,
          models: sub.models.map(m => {
            usedDeviceIds.add(m.id);
            return deviceMap.get(m.id) || m;
          }),
        })),
      };
    }
    if (cat.models) {
      return {
        ...cat,
        models: cat.models.map(m => {
          usedDeviceIds.add(m.id);
          return deviceMap.get(m.id) || m;
        }),
      };
    }
    return cat;
  });

  // Add devices from image library that aren't in vendor categories
  const extraDevices = deviceModels.filter(d => !usedDeviceIds.has(d.id));
  if (extraDevices.length > 0) {
    enrichedCategories.push({
      name: 'Other',
      models: extraDevices,
    });
  }

  return enrichedCategories;
}
