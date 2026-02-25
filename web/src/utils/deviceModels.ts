/**
 * Device Model Utilities
 *
 * Shared functions for building and transforming device model data.
 * Used by DeviceCatalogContext to provide consistent device data across the app.
 */

import { DeviceModel, ImageLibraryEntry } from '../studio/types';
import { DeviceCategory } from '../studio/constants';

const INSTANTIABLE_IMAGE_KINDS = new Set(['docker', 'qcow2']);

export type ImageCompatibilityAliasMap = Record<string, string[]>;

const EMPTY_IMAGE_COMPAT_ALIASES: ImageCompatibilityAliasMap = Object.freeze({});

interface CompatibilityLookup {
  direct: Map<string, string[]>;
  reverse: Map<string, string[]>;
  tokenCache: Map<string, Set<string>>;
}

const compatibilityLookupCache = new WeakMap<ImageCompatibilityAliasMap, CompatibilityLookup>();

function normalizeImageKind(kind?: string | null): string {
  return (kind || '').toLowerCase();
}

export function isInstantiableImageKind(kind?: string | null): boolean {
  return INSTANTIABLE_IMAGE_KINDS.has(normalizeImageKind(kind));
}

function normalizeKinds(kinds?: string[] | null): string[] {
  return (kinds || []).map((kind) => normalizeImageKind(kind));
}

/**
 * Return instantiable image kinds a device accepts.
 *
 * If supportedImageKinds is omitted, treat docker/qcow2 as default.
 * If supportedImageKinds is explicitly provided but contains no instantiable kinds
 * (for example ["iol"]), return an empty set.
 */
export function getAllowedInstantiableImageKinds(
  model: Pick<DeviceModel, 'supportedImageKinds'>
): Set<string> {
  if (model.supportedImageKinds && model.supportedImageKinds.length > 0) {
    return new Set(
      normalizeKinds(model.supportedImageKinds).filter((kind) =>
        isInstantiableImageKind(kind)
      )
    );
  }
  return new Set(['docker', 'qcow2']);
}

/**
 * Whether a device should require a runnable (docker/qcow2) image before add.
 *
 * IOL devices are treated as requiring a runnable image even when requiresImage
 * metadata is missing, because raw IOL binaries are never directly instantiable.
 */
export function requiresRunnableImage(
  model: Pick<DeviceModel, 'id' | 'kind' | 'tags' | 'requiresImage' | 'supportedImageKinds'>
): boolean {
  if (Boolean(model.requiresImage)) {
    return true;
  }

  const supportedKinds = normalizeKinds(model.supportedImageKinds);
  if (supportedKinds.includes('iol')) {
    return true;
  }

  const idToken = normalizeImageKind(model.id);
  const kindToken = normalizeImageKind(model.kind);
  const tagTokens = (model.tags || []).map((tag) => normalizeImageKind(tag));
  return (
    idToken.startsWith('iol') ||
    kindToken.startsWith('iol') ||
    tagTokens.includes('iol')
  );
}

/**
 * Get all device IDs an image is compatible with.
 * Uses compatible_devices when available, falls back to device_id.
 */
export function getImageDeviceIds(image: ImageLibraryEntry): string[] {
  if (image.compatible_devices?.length) return image.compatible_devices;
  return image.device_id ? [image.device_id] : [];
}

export function buildImageCompatibilityAliasMap(
  devices: Iterable<Pick<DeviceModel, 'id' | 'compatibilityAliases'>>
): ImageCompatibilityAliasMap {
  const aliasMap: ImageCompatibilityAliasMap = {};
  for (const device of devices) {
    const deviceId = normalizeDeviceToken(device.id);
    if (!deviceId) continue;

    const aliases = new Set<string>();
    (device.compatibilityAliases || []).forEach((alias) => {
      const normalized = normalizeDeviceToken(alias);
      if (!normalized || normalized === deviceId) return;
      aliases.add(normalized);
    });
    if (aliases.size > 0) {
      aliasMap[deviceId] = Array.from(aliases);
    }
  }
  return aliasMap;
}

function normalizeDeviceToken(deviceId?: string | null): string | null {
  if (!deviceId) return null;
  const normalized = String(deviceId).trim().toLowerCase();
  return normalized || null;
}

function getCompatibilityLookup(
  compatibilityAliases?: ImageCompatibilityAliasMap
): CompatibilityLookup {
  const aliasMap = compatibilityAliases || EMPTY_IMAGE_COMPAT_ALIASES;
  const cached = compatibilityLookupCache.get(aliasMap);
  if (cached) return cached;

  const direct = new Map<string, string[]>();
  const reverseBuckets = new Map<string, Set<string>>();
  Object.entries(aliasMap).forEach(([targetId, rawAliases]) => {
    const normalizedTarget = normalizeDeviceToken(targetId);
    if (!normalizedTarget) return;

    const aliasBucket = new Set<string>();
    (rawAliases || []).forEach((alias) => {
      const normalizedAlias = normalizeDeviceToken(alias);
      if (!normalizedAlias || normalizedAlias === normalizedTarget) return;
      aliasBucket.add(normalizedAlias);

      const reverse = reverseBuckets.get(normalizedAlias) || new Set<string>();
      reverse.add(normalizedTarget);
      reverseBuckets.set(normalizedAlias, reverse);
    });

    if (aliasBucket.size > 0) {
      direct.set(normalizedTarget, Array.from(aliasBucket));
    }
  });

  const reverse = new Map<string, string[]>();
  reverseBuckets.forEach((targets, token) => {
    reverse.set(token, Array.from(targets));
  });

  const lookup = { direct, reverse, tokenCache: new Map<string, Set<string>>() };
  compatibilityLookupCache.set(aliasMap, lookup);
  return lookup;
}

function getDeviceCompatibilityTokens(
  deviceId?: string | null,
  compatibilityAliases?: ImageCompatibilityAliasMap
): Set<string> {
  const normalized = normalizeDeviceToken(deviceId);
  if (!normalized) return new Set<string>();

  const lookup = getCompatibilityLookup(compatibilityAliases);
  const cached = lookup.tokenCache.get(normalized);
  if (cached) return cached;

  const tokens = new Set<string>([normalized]);

  // Direct aliases: canonical model -> shared/legacy token(s).
  (lookup.direct.get(normalized) || []).forEach((token) => tokens.add(token));

  // Reverse aliases: shared/legacy token -> one or more canonical models.
  (lookup.reverse.get(normalized) || []).forEach((token) => tokens.add(token));

  lookup.tokenCache.set(normalized, tokens);
  return tokens;
}

function getImageCompatibilityTokens(
  image: ImageLibraryEntry,
  compatibilityAliases?: ImageCompatibilityAliasMap
): Set<string> {
  const tokens = new Set<string>();
  getImageDeviceIds(image).forEach((id) => {
    getDeviceCompatibilityTokens(id, compatibilityAliases).forEach((token) => tokens.add(token));
  });
  return tokens;
}

/**
 * Return true when the image is compatible with a device model ID.
 */
export function imageMatchesDeviceId(
  image: ImageLibraryEntry,
  deviceId?: string | null,
  compatibilityAliases?: ImageCompatibilityAliasMap
): boolean {
  const targetTokens = getDeviceCompatibilityTokens(deviceId, compatibilityAliases);
  if (targetTokens.size === 0) return false;
  const imageTokens = getImageCompatibilityTokens(image, compatibilityAliases);

  return Array.from(targetTokens).some((token) => imageTokens.has(token));
}

function dedupeDeviceIds(deviceIds: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  deviceIds.forEach((deviceId) => {
    const normalized = normalizeDeviceToken(deviceId);
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    result.push(deviceId);
  });
  return result;
}

/**
 * Resolve an image's compatible device IDs against a known catalog.
 *
 * Keeps raw image IDs and adds matching known IDs via compatibility aliases.
 */
export function resolveImageDeviceIdsForCatalog(
  image: ImageLibraryEntry,
  knownDeviceIds: Iterable<string>,
  compatibilityAliases?: ImageCompatibilityAliasMap
): string[] {
  const resolved = buildResolvedImageDeviceIdsIndex([image], knownDeviceIds, compatibilityAliases);
  return resolved.get(image.id) || [];
}

/**
 * Build a memoizable map of image_id -> resolved compatible device IDs.
 *
 * This pre-indexes compatibility tokens so large image catalogs avoid
 * repeated O(images x devices) matching work across components.
 */
export function buildResolvedImageDeviceIdsIndex(
  images: ImageLibraryEntry[],
  knownDeviceIds: Iterable<string>,
  compatibilityAliases?: ImageCompatibilityAliasMap
): Map<string, string[]> {
  const knownByToken = new Map<string, Set<string>>();
  for (const knownId of knownDeviceIds) {
    const candidate = String(knownId || '').trim();
    if (!candidate) continue;
    getDeviceCompatibilityTokens(candidate, compatibilityAliases).forEach((token) => {
      const bucket = knownByToken.get(token) || new Set<string>();
      bucket.add(candidate);
      knownByToken.set(token, bucket);
    });
  }

  const resolvedByImageId = new Map<string, string[]>();
  images.forEach((image) => {
    const rawIds = getImageDeviceIds(image).map((id) => String(id));
    const resolved: string[] = [...rawIds];
    const seen = new Set<string>(
      rawIds
        .map((id) => normalizeDeviceToken(id))
        .filter((id): id is string => Boolean(id))
    );

    getImageCompatibilityTokens(image, compatibilityAliases).forEach((token) => {
      const bucket = knownByToken.get(token);
      if (!bucket) return;
      bucket.forEach((knownId) => {
        const normalized = normalizeDeviceToken(knownId);
        if (!normalized || seen.has(normalized)) return;
        seen.add(normalized);
        resolved.push(knownId);
      });
    });

    resolvedByImageId.set(image.id, dedupeDeviceIds(resolved));
  });

  return resolvedByImageId;
}

export function normalizeDefaultDeviceScopeId(deviceId?: string | null): string | null {
  if (!deviceId) return null;
  const normalized = String(deviceId).trim().toLowerCase();
  return normalized || null;
}

export function getImageDefaultDeviceIds(image: ImageLibraryEntry): string[] {
  const scopedDefaults = (image.default_for_devices || [])
    .map((id) => normalizeDefaultDeviceScopeId(id))
    .filter((id): id is string => Boolean(id));
  if (scopedDefaults.length > 0) {
    return Array.from(new Set(scopedDefaults));
  }

  // Legacy fallback for entries that only tracked a boolean default flag.
  if (image.is_default && image.device_id) {
    const scope = normalizeDefaultDeviceScopeId(image.device_id);
    return scope ? [scope] : [];
  }

  return [];
}

export function isImageDefaultForDevice(image: ImageLibraryEntry, deviceId?: string | null): boolean {
  const scope = normalizeDefaultDeviceScopeId(deviceId);
  if (!scope) return false;
  return getImageDefaultDeviceIds(image).includes(scope);
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
