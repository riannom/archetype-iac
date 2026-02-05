import { DEVICE_CATEGORIES, DEVICE_MODELS } from './constants';

describe('studio constants', () => {
  it('exposes device categories and flattened models', () => {
    expect(DEVICE_CATEGORIES.length).toBeGreaterThan(0);
    expect(DEVICE_MODELS.length).toBeGreaterThan(0);
  });
});
