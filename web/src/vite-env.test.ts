import * as viteEnv from './vite-env.d';

describe('vite-env', () => {
  it('loads environment typings', () => {
    void viteEnv;
    expect(true).toBe(true);
  });
});
