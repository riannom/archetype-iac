import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from 'react';
import { apiRequest } from '../api';
import { ImageLibraryEntry } from '../studio/types';

export interface ImageLibraryContextType {
  imageLibrary: ImageLibraryEntry[];
  loading: boolean;
  error: string | null;
  refreshImageLibrary: () => Promise<void>;
}

const ImageLibraryContext = createContext<ImageLibraryContextType | null>(null);

interface ImageLibraryProviderProps {
  children: React.ReactNode;
}

export function ImageLibraryProvider({ children }: ImageLibraryProviderProps) {
  const [imageLibrary, setImageLibrary] = useState<ImageLibraryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchImageLibrary = useCallback(async () => {
    try {
      const data = await apiRequest<{ images?: ImageLibraryEntry[] }>('/images/library');
      setImageLibrary(data.images || []);
      setError(null);
    } catch (err) {
      console.error('Failed to fetch image library:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch image library');
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshImageLibrary = useCallback(async () => {
    await fetchImageLibrary();
  }, [fetchImageLibrary]);

  // Fetch on mount
  useEffect(() => {
    fetchImageLibrary();
  }, [fetchImageLibrary]);

  // Refetch when auth token changes (e.g., after login)
  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'token' && e.newValue) {
        fetchImageLibrary();
      }
    };
    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, [fetchImageLibrary]);

  const contextValue: ImageLibraryContextType = useMemo(() => ({
    imageLibrary,
    loading,
    error,
    refreshImageLibrary,
  }), [imageLibrary, loading, error, refreshImageLibrary]);

  return (
    <ImageLibraryContext.Provider value={contextValue}>
      {children}
    </ImageLibraryContext.Provider>
  );
}

/**
 * Hook to access image library context
 */
export function useImageLibrary(): ImageLibraryContextType {
  const context = useContext(ImageLibraryContext);
  if (!context) {
    throw new Error('useImageLibrary must be used within an ImageLibraryProvider');
  }
  return context;
}
