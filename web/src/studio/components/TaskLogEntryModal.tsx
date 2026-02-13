import React, { useEffect, useRef, useState } from 'react';
import DetailPopup from './DetailPopup';
import type { TaskLogEntry } from './TaskLogPanel';

interface TaskLogEntryModalProps {
  isOpen: boolean;
  onClose: () => void;
  entry: TaskLogEntry | null;
}

const TaskLogEntryModal: React.FC<TaskLogEntryModalProps> = ({ isOpen, onClose, entry }) => {
  const [copyStatus, setCopyStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const copyTimeoutRef = useRef<number | null>(null);

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
    if (!entry) return;
    const copyText = [
      entry.timestamp.toISOString(),
      `[${entry.level.toUpperCase()}]`,
      entry.message,
      entry.jobId ? `(jobId=${entry.jobId})` : '',
    ]
      .filter(Boolean)
      .join(' ');

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(copyText);
        setCopyStatusWithTimeout('success');
        return;
      }
    } catch {
      // Fall back below
    }

    const success = fallbackCopy(copyText);
    setCopyStatusWithTimeout(success ? 'success' : 'error');
  };

  const title = entry ? `Task Log (${entry.level})` : 'Task Log';

  return (
    <DetailPopup isOpen={isOpen} onClose={onClose} title={title} width="max-w-3xl">
      {!entry ? (
        <div className="py-8 text-center text-sm text-stone-500 dark:text-stone-400">
          No entry selected.
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs text-stone-500 dark:text-stone-400 font-mono">
              {entry.timestamp.toLocaleString()}
              {entry.jobId ? ` • job: ${entry.jobId}` : ''}
            </div>
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

          <div className="overflow-auto bg-stone-950 rounded-lg border border-stone-800">
            <pre className="p-4 text-xs font-mono text-stone-200 whitespace-pre-wrap break-words">
              {entry.message}
            </pre>
          </div>
        </div>
      )}
    </DetailPopup>
  );
};

export default TaskLogEntryModal;

