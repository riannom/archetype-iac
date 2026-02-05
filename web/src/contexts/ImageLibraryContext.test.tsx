import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import { ImageLibraryProvider, useImageLibrary } from './ImageLibraryContext';

const apiRequest = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

function Consumer() {
  const { loading, imageLibrary } = useImageLibrary();
  if (loading) {
    return <div>loading</div>;
  }
  return <div>count:{imageLibrary.length}</div>;
}

describe('ImageLibraryContext', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('loads image library data', async () => {
    apiRequest.mockResolvedValue({
      images: [{ id: 'img1', kind: 'docker', reference: 'ref1' }],
    });

    render(
      <ImageLibraryProvider>
        <Consumer />
      </ImageLibraryProvider>
    );

    await waitFor(() => expect(screen.getByText('count:1')).toBeInTheDocument());
  });

  it('handles fetch errors', async () => {
    apiRequest.mockRejectedValue(new Error('fail'));

    render(
      <ImageLibraryProvider>
        <Consumer />
      </ImageLibraryProvider>
    );

    await waitFor(() => expect(screen.getByText('count:0')).toBeInTheDocument());
  });
});
