import { APP_VERSION } from './config';

describe('config', () => {
  it('exports version constant', () => {
    expect(APP_VERSION).toBeTruthy();
  });
});
