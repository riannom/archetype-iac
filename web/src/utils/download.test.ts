import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { downloadBlob } from './download';

describe('downloadBlob', () => {
  let createObjectURLSpy: ReturnType<typeof vi.fn>;
  let revokeObjectURLSpy: ReturnType<typeof vi.fn>;
  let appendChildSpy: ReturnType<typeof vi.spyOn>;
  let removeChildSpy: ReturnType<typeof vi.spyOn>;
  let clickSpy: ReturnType<typeof vi.fn>;
  let createdAnchor: HTMLAnchorElement;

  beforeEach(() => {
    createObjectURLSpy = vi.fn().mockReturnValue('blob:http://localhost/fake-url');
    revokeObjectURLSpy = vi.fn();
    clickSpy = vi.fn();

    global.URL.createObjectURL = createObjectURLSpy;
    global.URL.revokeObjectURL = revokeObjectURLSpy;

    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'a') {
        createdAnchor = originalCreateElement('a');
        createdAnchor.click = clickSpy;
        return createdAnchor;
      }
      return originalCreateElement(tag);
    });

    appendChildSpy = vi.spyOn(document.body, 'appendChild');
    removeChildSpy = vi.spyOn(document.body, 'removeChild');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates an object URL from the blob', () => {
    const blob = new Blob(['test content'], { type: 'text/plain' });
    downloadBlob(blob, 'test.txt');

    expect(createObjectURLSpy).toHaveBeenCalledWith(blob);
  });

  it('sets href and download attributes on the anchor element', () => {
    const blob = new Blob(['data'], { type: 'application/json' });
    downloadBlob(blob, 'config.json');

    expect(createdAnchor.href).toContain('blob:');
    expect(createdAnchor.download).toBe('config.json');
  });

  it('appends anchor to body, clicks it, then removes it', () => {
    const blob = new Blob(['yaml'], { type: 'text/yaml' });
    downloadBlob(blob, 'topology.yml');

    expect(appendChildSpy).toHaveBeenCalledWith(createdAnchor);
    expect(clickSpy).toHaveBeenCalled();
    expect(removeChildSpy).toHaveBeenCalledWith(createdAnchor);
  });

  it('revokes the object URL after download', () => {
    const blob = new Blob(['content']);
    downloadBlob(blob, 'file.bin');

    expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/fake-url');
  });

  it('calls operations in the correct order', () => {
    const callOrder: string[] = [];

    createObjectURLSpy.mockImplementation(() => {
      callOrder.push('createObjectURL');
      return 'blob:fake';
    });
    appendChildSpy.mockImplementation((node) => {
      callOrder.push('appendChild');
      return node;
    });
    clickSpy.mockImplementation(() => {
      callOrder.push('click');
    });
    removeChildSpy.mockImplementation((node) => {
      callOrder.push('removeChild');
      return node;
    });
    revokeObjectURLSpy.mockImplementation(() => {
      callOrder.push('revokeObjectURL');
    });

    downloadBlob(new Blob(['']), 'empty.txt');

    expect(callOrder).toEqual([
      'createObjectURL',
      'appendChild',
      'click',
      'removeChild',
      'revokeObjectURL',
    ]);
  });

  it('handles various content types', () => {
    const types = [
      { type: 'text/plain', ext: 'log' },
      { type: 'application/octet-stream', ext: 'bin' },
      { type: 'image/png', ext: 'png' },
    ];

    for (const { type, ext } of types) {
      const blob = new Blob(['data'], { type });
      downloadBlob(blob, `file.${ext}`);
      expect(createObjectURLSpy).toHaveBeenCalledWith(blob);
    }
  });
});
