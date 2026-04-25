import React from 'react';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
      throw new Error(`unexpected path: ${path}`);
    });

    render(
      <ImageLibraryProvider>
        <Consumer />
      </ImageLibraryProvider>
    );

    await waitFor(() => expect(screen.getByText('count:1;stale:0')).toBeInTheDocument());
  });

  it('treats a payload with no images field as an empty list', async () => {
    apiRequest.mockResolvedValueOnce({});

    render(
      <ImageLibraryProvider>
        <Consumer />
      </ImageLibraryProvider>
    );

    await waitFor(() => expect(screen.getByText('count:0;stale:0')).toBeInTheDocument());
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

  it('falls back to a generic error message for non-Error rejections', async () => {
    apiRequest.mockRejectedValue('boom');

    function ErrorConsumer() {
      const { error } = useImageLibrary();
      return <div data-testid="error">{error ?? 'no-error'}</div>;
    }

    render(
      <ImageLibraryProvider>
        <ErrorConsumer />
      </ImageLibraryProvider>
    );

    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('Failed to fetch image library'),
    );
  });

  describe('refreshImageLibrary', () => {
    it('re-fetches and updates state on subsequent calls', async () => {
      apiRequest.mockResolvedValueOnce({ images: [] });

      function RefreshConsumer() {
        const { imageLibrary, refreshImageLibrary } = useImageLibrary();
        return (
          <div>
            <span data-testid="count">{imageLibrary.length}</span>
            <button data-testid="refresh" onClick={() => refreshImageLibrary()}>
              refresh
            </button>
          </div>
        );
      }

      const user = userEvent.setup();
      render(
        <ImageLibraryProvider>
          <RefreshConsumer />
        </ImageLibraryProvider>
      );

      await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('0'));

      apiRequest.mockResolvedValueOnce({
        images: [
          { id: 'a', kind: 'docker', reference: 'r1' },
          { id: 'b', kind: 'docker', reference: 'r2' },
        ],
      });

      await user.click(screen.getByTestId('refresh'));
      await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('2'));
    });
  });

  describe('refreshStaleAgentSummary', () => {
    it('populates staleAgentSummary on success', async () => {
      apiRequest.mockImplementation(async (path: string) => {
        if (path === '/images/library') return { images: [] };
        if (path === '/agents/images/stale-summary') {
          return { total_stale_images: 7, agents: [] };
        }
        throw new Error('unexpected');
      });

      function StaleConsumer() {
        const { staleAgentSummary, refreshStaleAgentSummary } = useImageLibrary();
        return (
          <div>
            <span data-testid="stale">{staleAgentSummary?.total_stale_images ?? 0}</span>
            <button data-testid="refresh-stale" onClick={() => refreshStaleAgentSummary()}>
              refresh
            </button>
          </div>
        );
      }

      const user = userEvent.setup();
      render(
        <ImageLibraryProvider>
          <StaleConsumer />
        </ImageLibraryProvider>
      );

      await waitFor(() => expect(screen.getByTestId('stale')).toHaveTextContent('0'));
      await user.click(screen.getByTestId('refresh-stale'));
      await waitFor(() => expect(screen.getByTestId('stale')).toHaveTextContent('7'));
    });

    it('swallows errors silently (logs but does not throw)', async () => {
      apiRequest.mockImplementation(async (path: string) => {
        if (path === '/images/library') return { images: [] };
        throw new Error('stale boom');
      });

      function StaleConsumer() {
        const { staleAgentSummary, refreshStaleAgentSummary } = useImageLibrary();
        return (
          <div>
            <span data-testid="stale">{staleAgentSummary?.total_stale_images ?? 'null'}</span>
            <button data-testid="refresh-stale" onClick={() => refreshStaleAgentSummary()}>
              refresh
            </button>
          </div>
        );
      }

      const user = userEvent.setup();
      render(
        <ImageLibraryProvider>
          <StaleConsumer />
        </ImageLibraryProvider>
      );

      await waitFor(() => expect(screen.getByTestId('stale')).toHaveTextContent('null'));
      await expect(user.click(screen.getByTestId('refresh-stale'))).resolves.not.toThrow();
      // staleAgentSummary remains null after a failed refresh
      expect(screen.getByTestId('stale')).toHaveTextContent('null');
    });
  });

  describe('storage listener', () => {
    it('refetches the library when a new token appears in another tab', async () => {
      apiRequest.mockResolvedValueOnce({ images: [] });

      render(
        <ImageLibraryProvider>
          <Consumer />
        </ImageLibraryProvider>
      );
      await waitFor(() => expect(screen.getByText('count:0;stale:0')).toBeInTheDocument());

      apiRequest.mockResolvedValueOnce({
        images: [{ id: 'x', kind: 'docker', reference: 'after-login' }],
      });

      await act(async () => {
        window.dispatchEvent(
          new StorageEvent('storage', { key: 'token', newValue: 'fresh-token' }),
        );
      });

      await waitFor(() => expect(screen.getByText('count:1;stale:0')).toBeInTheDocument());
    });

    it('ignores storage events for other keys or with a null newValue', async () => {
      apiRequest.mockResolvedValueOnce({ images: [] });

      render(
        <ImageLibraryProvider>
          <Consumer />
        </ImageLibraryProvider>
      );
      await waitFor(() => expect(screen.getByText('count:0;stale:0')).toBeInTheDocument());

      apiRequest.mockClear();

      await act(async () => {
        window.dispatchEvent(
          new StorageEvent('storage', { key: 'unrelated', newValue: 'nope' }),
        );
        // token cleared (logout) — newValue is null, must NOT refetch
        window.dispatchEvent(
          new StorageEvent('storage', { key: 'token', newValue: null }),
        );
      });

      expect(apiRequest).not.toHaveBeenCalled();
    });
  });

  describe('useImageLibrary outside provider', () => {
    it('throws a descriptive error', () => {
      expect(() => renderHook(() => useImageLibrary())).toThrow(
        /useImageLibrary must be used within an ImageLibraryProvider/,
      );
    });
  });
});
