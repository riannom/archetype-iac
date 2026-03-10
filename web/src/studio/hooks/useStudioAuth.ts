import { useState, useCallback } from 'react';
import { API_BASE_URL, apiRequest } from '../../api';

export function useStudioAuth() {
  const [authRequired, setAuthRequired] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(false);

  const studioRequest = useCallback(
    async <T,>(path: string, options: RequestInit = {}) => {
      try {
        return await apiRequest<T>(path, options);
      } catch (error) {
        if (error instanceof Error && error.message.toLowerCase().includes('unauthorized')) {
          setAuthRequired(true);
        }
        throw error;
      }
    },
    []
  );

  /**
   * Attempt login. On success, stores token and returns true.
   * On failure, sets authError and returns false.
   * Callers should perform post-login work (loadLabs, refreshUser, etc.) on success.
   */
  const handleLogin = useCallback(
    async (username: string, password?: string): Promise<boolean> => {
      setAuthError(null);
      setAuthLoading(true);
      try {
        const body = new URLSearchParams();
        body.set('username', username);
        body.set('password', password || '');
        const response = await fetch(`${API_BASE_URL}/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: body.toString(),
        });
        if (!response.ok) {
          const message = await response.text();
          throw new Error(message || 'Login failed');
        }
        const data = (await response.json()) as { access_token?: string };
        if (!data.access_token) {
          throw new Error('Login failed');
        }
        localStorage.setItem('token', data.access_token);
        setAuthRequired(false);
        return true;
      } catch (error) {
        setAuthError(error instanceof Error ? error.message : 'Login failed');
        return false;
      } finally {
        setAuthLoading(false);
      }
    },
    []
  );

  const beginLogout = useCallback(() => {
    localStorage.removeItem('token');
    setAuthRequired(true);
    setAuthError(null);
  }, []);

  return {
    authRequired,
    authError,
    authLoading,
    studioRequest,
    handleLogin,
    /** Clears token and sets authRequired. Caller should also clearUser, setActiveLab(null), etc. */
    beginLogout,
  };
}
