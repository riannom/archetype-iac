import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

vi.mock('../../api', () => ({
  API_BASE_URL: '/api',
  apiRequest: vi.fn(),
}));

import { useStudioAuth } from './useStudioAuth';
import * as api from '../../api';

const mockedApiRequest = api.apiRequest as unknown as ReturnType<typeof vi.fn>;

describe('useStudioAuth', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    localStorage.clear();
    mockedApiRequest.mockReset();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('starts with no auth required, no error, not loading', () => {
    const { result } = renderHook(() => useStudioAuth());
    expect(result.current.authRequired).toBe(false);
    expect(result.current.authError).toBeNull();
    expect(result.current.authLoading).toBe(false);
  });

  describe('studioRequest', () => {
    it('passes through the apiRequest result', async () => {
      mockedApiRequest.mockResolvedValueOnce({ ok: true });
      const { result } = renderHook(() => useStudioAuth());

      const value = await result.current.studioRequest('/labs');
      expect(value).toEqual({ ok: true });
      expect(mockedApiRequest).toHaveBeenCalledWith('/labs', {});
    });

    it('forwards request options to apiRequest', async () => {
      mockedApiRequest.mockResolvedValueOnce(null);
      const { result } = renderHook(() => useStudioAuth());

      await result.current.studioRequest('/labs', { method: 'POST' });
      expect(mockedApiRequest).toHaveBeenCalledWith('/labs', { method: 'POST' });
    });

    it('sets authRequired and rethrows when apiRequest reports unauthorized', async () => {
      mockedApiRequest.mockRejectedValueOnce(new Error('Unauthorized: token expired'));
      const { result } = renderHook(() => useStudioAuth());

      await expect(result.current.studioRequest('/labs')).rejects.toThrow(/unauthorized/i);
      await waitFor(() => expect(result.current.authRequired).toBe(true));
    });

    it('rethrows non-auth errors without flipping authRequired', async () => {
      mockedApiRequest.mockRejectedValueOnce(new Error('Server exploded'));
      const { result } = renderHook(() => useStudioAuth());

      await expect(result.current.studioRequest('/labs')).rejects.toThrow('Server exploded');
      expect(result.current.authRequired).toBe(false);
    });

    it('rethrows non-Error rejections without flipping authRequired', async () => {
      mockedApiRequest.mockRejectedValueOnce('boom');
      const { result } = renderHook(() => useStudioAuth());

      await expect(result.current.studioRequest('/labs')).rejects.toBe('boom');
      expect(result.current.authRequired).toBe(false);
    });
  });

  describe('handleLogin', () => {
    it('stores token, clears authRequired, returns true on success', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ access_token: 'abc' }),
      }) as unknown as typeof fetch;

      const { result } = renderHook(() => useStudioAuth());
      // Pre-set authRequired so we can verify it gets cleared
      mockedApiRequest.mockRejectedValueOnce(new Error('unauthorized'));
      await result.current.studioRequest('/labs').catch(() => {});
      await waitFor(() => expect(result.current.authRequired).toBe(true));

      let success = false;
      await act(async () => {
        success = await result.current.handleLogin('alice', 'pw');
      });

      expect(success).toBe(true);
      expect(localStorage.getItem('token')).toBe('abc');
      expect(result.current.authRequired).toBe(false);
      expect(result.current.authError).toBeNull();
      expect(result.current.authLoading).toBe(false);

      const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/auth/login',
        expect.objectContaining({ method: 'POST' }),
      );
      const sentBody = (fetchMock.mock.calls[0][1] as RequestInit).body;
      expect(String(sentBody)).toBe('username=alice&password=pw');
    });

    it('treats missing password as empty string', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ access_token: 't' }),
      }) as unknown as typeof fetch;

      const { result } = renderHook(() => useStudioAuth());
      await act(async () => {
        await result.current.handleLogin('bob');
      });

      const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
      const sentBody = (fetchMock.mock.calls[0][1] as RequestInit).body;
      expect(String(sentBody)).toBe('username=bob&password=');
    });

    it('returns false and surfaces server message when response is not ok', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false,
        text: async () => 'Bad credentials',
      }) as unknown as typeof fetch;

      const { result } = renderHook(() => useStudioAuth());
      let success = true;
      await act(async () => {
        success = await result.current.handleLogin('alice', 'pw');
      });

      expect(success).toBe(false);
      expect(result.current.authError).toBe('Bad credentials');
      expect(localStorage.getItem('token')).toBeNull();
    });

    it('falls back to "Login failed" when server returns no message', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false,
        text: async () => '',
      }) as unknown as typeof fetch;

      const { result } = renderHook(() => useStudioAuth());
      await act(async () => {
        await result.current.handleLogin('alice', 'pw');
      });
      expect(result.current.authError).toBe('Login failed');
    });

    it('returns false when access_token is missing on a 200 response', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({}),
      }) as unknown as typeof fetch;

      const { result } = renderHook(() => useStudioAuth());
      let success = true;
      await act(async () => {
        success = await result.current.handleLogin('alice', 'pw');
      });

      expect(success).toBe(false);
      expect(result.current.authError).toBe('Login failed');
      expect(localStorage.getItem('token')).toBeNull();
    });

    it('falls back to "Login failed" when the thrown error is not an Error instance', async () => {
      globalThis.fetch = vi.fn().mockRejectedValue('network down') as unknown as typeof fetch;

      const { result } = renderHook(() => useStudioAuth());
      await act(async () => {
        await result.current.handleLogin('alice', 'pw');
      });

      expect(result.current.authError).toBe('Login failed');
    });
  });

  describe('beginLogout', () => {
    it('clears the stored token, sets authRequired, and clears authError', () => {
      localStorage.setItem('token', 'previous');
      const { result } = renderHook(() => useStudioAuth());

      act(() => {
        result.current.beginLogout();
      });

      expect(localStorage.getItem('token')).toBeNull();
      expect(result.current.authRequired).toBe(true);
      expect(result.current.authError).toBeNull();
    });
  });
});
