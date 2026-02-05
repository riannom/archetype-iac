import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import { DeviceCatalogProvider, useDeviceCatalog } from './DeviceCatalogContext';
import { DeviceType } from '../studio/types';

const apiRequest = vi.fn();
const refreshImageLibrary = vi.fn();
const initializePatterns = vi.fn();
const buildDeviceModels = vi.fn();
const enrichDeviceCategories = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

vi.mock('./ImageLibraryContext', () => ({
  useImageLibrary: () => ({
    imageLibrary: [{ id: 'img1', kind: 'docker', reference: 'ref1' }],
    refreshImageLibrary,
  }),
}));

vi.mock('../utils/deviceModels', () => ({
  buildDeviceModels: (...args: unknown[]) => buildDeviceModels(...args),
  enrichDeviceCategories: (...args: unknown[]) => enrichDeviceCategories(...args),
}));

vi.mock('../studio/utils/interfaceRegistry', () => ({
  initializePatterns: (...args: unknown[]) => initializePatterns(...args),
}));

function Consumer() {
  const { loading, vendorCategories, imageCatalog } = useDeviceCatalog();
  if (loading) {
    return <div>loading</div>;
  }
  return (
    <div>
      vendors:{vendorCategories.length}-images:{Object.keys(imageCatalog).length}
    </div>
  );
}

describe('DeviceCatalogContext', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    refreshImageLibrary.mockReset();
    initializePatterns.mockReset();
    buildDeviceModels.mockReset();
    enrichDeviceCategories.mockReset();
  });

  it('fetches vendor and image catalog data', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/vendors') {
        return [{ name: 'Compute', models: [{ id: 'linux' }] }];
      }
      if (path === '/images') {
        return { images: { linux: { clab: 'linux:latest' } } };
      }
      return null;
    });

    buildDeviceModels.mockReturnValue([
      {
        id: 'linux',
        type: DeviceType.HOST,
        name: 'Linux',
        icon: 'linux',
        versions: [],
        isActive: true,
        vendor: 'Linux',
      },
    ]);
    enrichDeviceCategories.mockReturnValue([{ name: 'Compute', models: [{ id: 'linux' }] }]);

    render(
      <DeviceCatalogProvider>
        <Consumer />
      </DeviceCatalogProvider>
    );

    await waitFor(() => expect(screen.getByText('vendors:1-images:1')).toBeInTheDocument());
    expect(refreshImageLibrary).toHaveBeenCalledTimes(1);
    expect(initializePatterns).toHaveBeenCalledTimes(1);
  });

  it('sets error state on fetch failure', async () => {
    apiRequest.mockRejectedValue(new Error('boom'));
    buildDeviceModels.mockReturnValue([]);
    enrichDeviceCategories.mockReturnValue([]);

    render(
      <DeviceCatalogProvider>
        <Consumer />
      </DeviceCatalogProvider>
    );

    await waitFor(() => expect(screen.getByText('vendors:0-images:0')).toBeInTheDocument());
  });
});
