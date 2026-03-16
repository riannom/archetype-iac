import React from 'react';
import { ISOFileInfo, InputMode, formatBytes } from './types';

interface ISOInputStepProps {
  inputMode: InputMode;
  setInputMode: (mode: InputMode) => void;
  isoPath: string;
  setIsoPath: (path: string) => void;
  availableISOs: ISOFileInfo[];
  loadingISOs: boolean;
  uploadDir: string;
  fetchAvailableISOs: () => void;
  selectedFile: File | null;
  setSelectedFile: (file: File | null) => void;
  fileInputRef: React.RefObject<HTMLInputElement>;
  handleFileSelect: (file: File) => void;
  error: string | null;
}

export const ISOInputStep: React.FC<ISOInputStepProps> = ({
  inputMode,
  setInputMode,
  isoPath,
  setIsoPath,
  availableISOs,
  loadingISOs,
  uploadDir,
  fetchAvailableISOs,
  selectedFile,
  setSelectedFile,
  fileInputRef,
  handleFileSelect,
  error,
}) => {
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) {
      handleFileSelect(file);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
  };

  return (
    <div className="space-y-4">
      {/* Mode tabs */}
      <div className="flex gap-1 p-1 bg-stone-100 dark:bg-stone-800 rounded-lg">
        <button
          onClick={() => setInputMode('browse')}
          className={`flex-1 px-3 py-2 text-xs font-bold rounded-md transition-all ${
            inputMode === 'browse'
              ? 'bg-white dark:bg-stone-700 text-stone-800 dark:text-white shadow-sm'
              : 'text-stone-500 hover:text-stone-700 dark:hover:text-stone-300'
          }`}
        >
          <i className="fa-solid fa-folder-open mr-2" />
          Browse Server
        </button>
        <button
          onClick={() => setInputMode('upload')}
          className={`flex-1 px-3 py-2 text-xs font-bold rounded-md transition-all ${
            inputMode === 'upload'
              ? 'bg-white dark:bg-stone-700 text-stone-800 dark:text-white shadow-sm'
              : 'text-stone-500 hover:text-stone-700 dark:hover:text-stone-300'
          }`}
        >
          <i className="fa-solid fa-upload mr-2" />
          Upload ISO
        </button>
        <button
          onClick={() => setInputMode('custom')}
          className={`flex-1 px-3 py-2 text-xs font-bold rounded-md transition-all ${
            inputMode === 'custom'
              ? 'bg-white dark:bg-stone-700 text-stone-800 dark:text-white shadow-sm'
              : 'text-stone-500 hover:text-stone-700 dark:hover:text-stone-300'
          }`}
        >
          <i className="fa-solid fa-keyboard mr-2" />
          Custom Path
        </button>
      </div>

      {/* Browse Server Mode */}
      {inputMode === 'browse' && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <label className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-wider">
              Available ISOs
            </label>
            <button
              onClick={fetchAvailableISOs}
              className="text-[11px] text-sage-600 dark:text-sage-400 hover:underline font-bold"
            >
              <i className="fa-solid fa-rotate mr-1" />
              Refresh
            </button>
          </div>

          {loadingISOs ? (
            <div className="flex items-center justify-center py-8">
              <i className="fa-solid fa-spinner fa-spin text-stone-400 mr-2" />
              <span className="text-xs text-stone-500">Loading...</span>
            </div>
          ) : availableISOs.length > 0 ? (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {availableISOs.map((iso) => (
                <button
                  key={iso.path}
                  onClick={() => setIsoPath(iso.path)}
                  className={`w-full text-left p-3 rounded-lg border transition-all ${
                    isoPath === iso.path
                      ? 'bg-sage-50 dark:bg-stone-800 border-sage-300 dark:border-sage-600'
                      : 'bg-white dark:bg-stone-800 border-stone-200 dark:border-stone-700 hover:border-stone-300 dark:hover:border-stone-600'
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <i className="fa-solid fa-compact-disc text-purple-500" />
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-bold text-stone-700 dark:text-stone-300 truncate">
                        {iso.name}
                      </div>
                      <div className="text-[11px] text-stone-400">
                        {formatBytes(iso.size_bytes)} | {new Date(iso.modified_at).toLocaleDateString()}
                      </div>
                    </div>
                    {isoPath === iso.path && (
                      <i className="fa-solid fa-check text-sage-500" />
                    )}
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 bg-stone-50 dark:bg-stone-800/50 rounded-lg border border-dashed border-stone-300 dark:border-stone-700">
              <i className="fa-solid fa-folder-open text-2xl text-stone-300 dark:text-stone-600 mb-2" />
              <p className="text-xs text-stone-500 dark:text-stone-400">No ISOs found in upload directory</p>
              <p className="text-[11px] text-stone-400 mt-1">
                Copy ISOs to: <code className="bg-stone-200 dark:bg-stone-700 px-1 rounded">{uploadDir}</code>
              </p>
            </div>
          )}
        </div>
      )}

      {/* Upload Mode */}
      {inputMode === 'upload' && (
        <div>
          <input
            type="file"
            ref={fileInputRef}
            accept=".iso"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFileSelect(file);
            }}
          />

          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onClick={() => fileInputRef.current?.click()}
            className={`cursor-pointer border-2 border-dashed rounded-lg p-8 text-center transition-all ${
              selectedFile
                ? 'border-sage-400 dark:border-sage-600 bg-sage-50 dark:bg-stone-800'
                : 'border-stone-300 dark:border-stone-600 hover:border-sage-400 hover:bg-stone-50 dark:hover:bg-stone-800'
            }`}
          >
            {selectedFile ? (
              <div>
                <i className="fa-solid fa-compact-disc text-3xl text-purple-500 mb-3" />
                <p className="text-sm font-bold text-stone-700 dark:text-stone-300">
                  {selectedFile.name}
                </p>
                <p className="text-xs text-stone-500 mt-1">{formatBytes(selectedFile.size)}</p>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedFile(null);
                  }}
                  className="mt-3 text-xs text-red-500 hover:text-red-600 font-bold"
                >
                  <i className="fa-solid fa-xmark mr-1" />
                  Remove
                </button>
              </div>
            ) : (
              <div>
                <i className="fa-solid fa-cloud-arrow-up text-3xl text-stone-300 dark:text-stone-600 mb-3" />
                <p className="text-sm font-bold text-stone-600 dark:text-stone-400">
                  Drop ISO file here or click to browse
                </p>
                <p className="text-xs text-stone-400 mt-1">
                  Supports resumable chunked uploads for large files
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Custom Path Input */}
      {inputMode === 'custom' && (
        <div>
          <label className="text-xs font-bold text-stone-500 dark:text-stone-400 uppercase tracking-wider block mb-2">
            Server ISO Path
          </label>
          <input
            type="text"
            value={isoPath}
            onChange={(e) => setIsoPath(e.target.value)}
            placeholder="/path/to/image.iso"
            className="w-full px-4 py-3 bg-stone-100 dark:bg-stone-800 border border-stone-200 dark:border-stone-700 rounded-lg text-sm text-stone-900 dark:text-white placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-sage-500/50"
          />
          <p className="text-[11px] text-stone-400 mt-2">
            Enter the full path to an ISO file already on the server.
          </p>
        </div>
      )}

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
          <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
        </div>
      )}

      <div className="bg-stone-50 dark:bg-stone-800/50 rounded-lg p-4">
        <h4 className="text-xs font-bold text-stone-600 dark:text-stone-300 mb-2">
          <i className="fa-solid fa-info-circle mr-2 text-sage-500" />
          Supported Formats
        </h4>
        <ul className="text-xs text-stone-500 dark:text-stone-400 space-y-1">
          <li>
            <i className="fa-solid fa-check text-emerald-500 mr-2" />
            Cisco VIRL2/CML2 (RefPlat ISOs)
          </li>
        </ul>
      </div>
    </div>
  );
};
