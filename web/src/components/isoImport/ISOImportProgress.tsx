import React from 'react';
import { ImageProgress, formatBytes } from './types';

interface ISOImportProgressProps {
  overallProgress: number;
  importProgress: Record<string, ImageProgress>;
}

export const ISOImportProgress: React.FC<ISOImportProgressProps> = ({
  overallProgress,
  importProgress,
}) => {
  return (
    <div className="space-y-6">
      <div className="text-center py-4">
        <i className="fa-solid fa-download fa-bounce text-3xl text-sage-500 mb-3" />
        <h3 className="text-sm font-bold text-stone-700 dark:text-stone-300">Importing Images...</h3>
        <p className="text-xs text-stone-500 mt-1">
          This may take a while for large images. Please don't close this window.
        </p>
      </div>

      {/* Overall progress */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="font-bold text-stone-600 dark:text-stone-400">Overall Progress</span>
          <span className="text-stone-500">{overallProgress}%</span>
        </div>
        <div className="h-2 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
          <div
            className="h-full bg-sage-500 transition-all duration-300"
            style={{ width: `${overallProgress}%` }}
          />
        </div>
      </div>

      {/* Per-image progress */}
      <div className="space-y-3">
        {Object.entries(importProgress).map(([imageId, progress]) => (
          <div key={imageId} className="bg-stone-50 dark:bg-stone-800/50 rounded-lg p-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-stone-700 dark:text-stone-300 truncate">
                {imageId}
              </span>
              <span
                className={`text-[10px] font-bold uppercase ${
                  progress.status === 'completed'
                    ? 'text-emerald-500'
                    : progress.status === 'failed'
                    ? 'text-red-500'
                    : 'text-sage-500'
                }`}
              >
                {progress.status === 'extracting' && (
                  <i className="fa-solid fa-spinner fa-spin mr-1" />
                )}
                {progress.status}
              </span>
            </div>
            <div className="h-1.5 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-300 ${
                  progress.status === 'completed'
                    ? 'bg-emerald-500'
                    : progress.status === 'failed'
                    ? 'bg-red-500'
                    : 'bg-sage-500'
                }`}
                style={{ width: `${progress.progress_percent}%` }}
              />
            </div>
            {progress.error_message && (
              <p className="text-[10px] text-red-500 mt-1">{progress.error_message}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

interface UploadProgressStepProps {
  uploadProgress: number;
  uploadStatus: string;
  selectedFile: File | null;
  cancelUpload: () => void;
}

export const UploadProgressStep: React.FC<UploadProgressStepProps> = ({
  uploadProgress,
  uploadStatus,
  selectedFile,
  cancelUpload,
}) => {
  return (
    <div className="space-y-6">
      <div className="text-center py-4">
        <i className="fa-solid fa-cloud-arrow-up fa-bounce text-3xl text-sage-500 mb-3" />
        <h3 className="text-sm font-bold text-stone-700 dark:text-stone-300">Uploading ISO...</h3>
        <p className="text-xs text-stone-500 mt-1">
          {uploadStatus || 'Preparing upload...'}
        </p>
      </div>

      {/* Upload progress */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="font-bold text-stone-600 dark:text-stone-400">Upload Progress</span>
          <span className="text-stone-500">{uploadProgress}%</span>
        </div>
        <div className="h-3 bg-stone-200 dark:bg-stone-700 rounded-full overflow-hidden">
          <div
            className="h-full bg-sage-500 transition-all duration-300"
            style={{ width: `${uploadProgress}%` }}
          />
        </div>
        {selectedFile && (
          <p className="text-[10px] text-stone-400 mt-2 text-center">
            {formatBytes((uploadProgress / 100) * selectedFile.size)} / {formatBytes(selectedFile.size)}
          </p>
        )}
      </div>

      <div className="text-center">
        <button
          onClick={cancelUpload}
          className="px-4 py-2 text-xs font-bold text-red-600 hover:text-red-700 transition-all"
        >
          <i className="fa-solid fa-xmark mr-2" />
          Cancel Upload
        </button>
      </div>
    </div>
  );
};
