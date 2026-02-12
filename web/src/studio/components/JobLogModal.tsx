import React, { useEffect, useRef, useState } from 'react';
import DetailPopup from './DetailPopup';

interface JobLogModalProps {
  isOpen: boolean;
  onClose: () => void;
  labId: string;
  jobId: string;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
}

const JobLogModal: React.FC<JobLogModalProps> = ({
  isOpen,
  onClose,
  labId,
  jobId,
  studioRequest,
}) => {
  const [log, setLog] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const copyTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    if (!isOpen || !labId || !jobId) return;

    const fetchLog = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await studioRequest<{ log: string }>(
          `/labs/${labId}/jobs/${jobId}/log`
        );
        setLog(data.log || '');
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to load job log';
        setError(message);
        setLog('');
      } finally {
        setLoading(false);
      }
    };

    fetchLog();
  }, [isOpen, labId, jobId, studioRequest]);

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        window.clearTimeout(copyTimeoutRef.current);
      }
    };
  }, []);

  const setCopyStatusWithTimeout = (status: 'success' | 'error') => {
    setCopyStatus(status);
    if (copyTimeoutRef.current) {
      window.clearTimeout(copyTimeoutRef.current);
    }
    copyTimeoutRef.current = window.setTimeout(() => {
      setCopyStatus('idle');
    }, 2000);
  };

  const fallbackCopy = (text: string) => {
    try {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.top = '-1000px';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      textarea.setSelectionRange(0, text.length);
      const success = document.execCommand('copy');
      document.body.removeChild(textarea);
      return success;
    } catch {
      return false;
    }
  };

  const handleCopy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(log);
        setCopyStatusWithTimeout('success');
        return;
      }
    } catch {
      // Fall back below
    }

    const success = fallbackCopy(log);
    if (success) {
      setCopyStatusWithTimeout('success');
    } else {
      setCopyStatusWithTimeout('error');
    }
  };

  return (
    <DetailPopup isOpen={isOpen} onClose={onClose} title="Job Log" width="max-w-4xl">
      {loading && (
        <div className="flex items-center justify-center py-12">
          <i className="fa-solid fa-spinner fa-spin text-2xl text-stone-400" />
        </div>
      )}

      {error && (
        <div className="py-12 text-center">
          <i className="fa-solid fa-exclamation-circle text-2xl text-red-500 mb-2" />
          <p className="text-sm text-stone-500 dark:text-stone-400">{error}</p>
        </div>
      )}

      {!loading && !error && !log && (
        <div className="py-12 text-center">
          <i className="fa-solid fa-file-lines text-3xl text-stone-300 dark:text-stone-700 mb-3" />
          <p className="text-sm text-stone-500 dark:text-stone-400">
            No log content available.
          </p>
        </div>
      )}

      {!loading && !error && log && (
        <div className="flex flex-col h-[60vh]">
          <div className="flex items-center justify-end mb-3">
            <button
              onClick={handleCopy}
              className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium glass-control text-stone-700 dark:text-stone-300 rounded-lg transition-colors"
            >
              <i
                className={`fa-solid ${
                  copyStatus === 'success' ? 'fa-check' : copyStatus === 'error' ? 'fa-triangle-exclamation' : 'fa-copy'
                }`}
              />
              {copyStatus === 'success' ? 'Copied!' : copyStatus === 'error' ? 'Copy failed' : 'Copy'}
            </button>
          </div>
          <div className="flex-1 overflow-auto bg-stone-950 rounded-lg border border-stone-800">
            <pre className="p-4 text-xs font-mono text-stone-300 whitespace-pre-wrap break-words">
              {log}
            </pre>
          </div>
        </div>
      )}
    </DetailPopup>
  );
};

export default JobLogModal;
