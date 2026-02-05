import { APP_VERSION, APP_VERSION_LABEL } from './config';

describe('config', () => {
  it('exports version constants', () => {
    expect(APP_VERSION).toBeTruthy();
    expect(APP_VERSION_LABEL).toBeTruthy();
  });
});
