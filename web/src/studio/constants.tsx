/**
 * Device Catalog Constants
 *
 * NOTE: Device categories are now dynamically loaded from the /vendors API endpoint.
 * The API returns data from the unified vendor registry (agent/vendors.py).
 * This file contains static fallback data and type exports.
 *
 * See: agent/vendors.py for the single source of truth
 * See: StudioPage.tsx for the API fetch implementation
 */

import { DeviceModel } from './types';

interface DeviceSubCategory {
  name: string;
  models: DeviceModel[];
}

export interface DeviceCategory {
  name: string;
  subCategories?: DeviceSubCategory[];
  models?: DeviceModel[];
}

