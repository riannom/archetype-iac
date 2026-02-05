import * as setupTests from './setupTests';

describe('setupTests', () => {
  it('sets up DOM mocks', () => {
    void setupTests;
    expect(window.matchMedia).toBeDefined();
    expect(window.ResizeObserver).toBeDefined();
    expect(Element.prototype.scrollIntoView).toBeDefined();
  });
});
