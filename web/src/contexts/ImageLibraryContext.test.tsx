import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import { ImageLibraryProvider, useImageLibrary } from './ImageLibraryContext';

const apiRequest = vi.fn();

vi.mock('../api', () => ({
  apiRequest: (...args: unknown[]) => apiRequest(...args),
}));

function Consumer() {
  const { loading, imageLibrary, staleAgentSummary } = useImageLibrary();
  if (loading) {
    return <div>loading</div>;
  }
  return <div>count:{imageLibrary.length};stale:{staleAgentSummary?.total_stale_images ?? 0}</div>;
}

describe('ImageLibraryContext', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('loads image library data', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/images/library') {
        return { images: [{ id: 'img1', kind: 'docker', reference: 'ref1' }] };
      }
      if (path === '/agents/images/stale-summary') {
        return { hosts: [], total_stale_images: 2, affected_agents: 1 };
      }
      throw new Error(`unexpected path: ${path}`);
    });

    render(
      <ImageLibraryProvider>
        <Consumer />
      </ImageLibraryProvider>
    );

    await waitFor(() => expect(screen.getByText('count:1;stale:2')).toBeInTheDocument());
  });

  it('handles fetch errors', async () => {
    apiRequest.mockRejectedValue(new Error('fail'));

    render(
      <ImageLibraryProvider>
        <Consumer />
      </ImageLibraryProvider>
    );

    await waitFor(() => expect(screen.getByText('count:0;stale:0')).toBeInTheDocument());
  });
});
