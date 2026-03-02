import React, { useEffect, useCallback } from 'react';
import { ISOImportModalProps, ISOImportLogEvent } from './types';
import { useISOUpload } from './useISOUpload';
import { ISOInputStep } from './ISOInputStep';
import { ISOReviewStep } from './ISOReviewStep';
import { ISOImportProgress, UploadProgressStep } from './ISOImportProgress';

const ISOImportModal: React.FC<ISOImportModalProps> = ({
  isOpen,
  onClose,
  onImportComplete,
  onLogEvent,
}) => {
  const logEvent = useCallback((event: ISOImportLogEvent) => {
    onLogEvent?.(event);
  }, [onLogEvent]);

  const upload = useISOUpload({ logEvent });

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      upload.resetState();
      upload.fetchAvailableISOs();
    }
    return () => {
      upload.cleanup();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-white dark:bg-stone-900 rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-stone-900 dark:text-white">Import from ISO</h2>
            <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
              Import VM images from vendor ISO files (Cisco RefPlat, etc.)
            </p>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 rounded-lg hover:bg-stone-100 dark:hover:bg-stone-800 transition-all"
          >
            <i className="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* Step 1: Input ISO Path */}
          {upload.step === 'input' && (
            <ISOInputStep
              inputMode={upload.inputMode}
              setInputMode={upload.setInputMode}
              isoPath={upload.isoPath}
              setIsoPath={upload.setIsoPath}
              availableISOs={upload.availableISOs}
              loadingISOs={upload.loadingISOs}
              uploadDir={upload.uploadDir}
              fetchAvailableISOs={upload.fetchAvailableISOs}
              selectedFile={upload.selectedFile}
              setSelectedFile={upload.setSelectedFile}
              fileInputRef={upload.fileInputRef}
              handleFileSelect={upload.handleFileSelect}
              error={upload.error}
            />
          )}

          {/* Step 1b: Uploading */}
          {upload.step === 'uploading' && (
            <UploadProgressStep
              uploadProgress={upload.uploadProgress}
              uploadStatus={upload.uploadStatus}
              selectedFile={upload.selectedFile}
              cancelUpload={upload.cancelUpload}
            />
          )}

          {/* Step 2: Scanning */}
          {upload.step === 'scanning' && (
            <div className="flex flex-col items-center justify-center py-12">
              <i className="fa-solid fa-compact-disc fa-spin text-4xl text-sage-500 mb-4" />
              <h3 className="text-sm font-bold text-stone-700 dark:text-stone-300">Scanning ISO...</h3>
              <p className="text-xs text-stone-500 mt-1">Parsing node definitions and images</p>
            </div>
          )}

          {/* Step 3: Review */}
          {upload.step === 'review' && upload.scanResult && (
            <ISOReviewStep
              scanResult={upload.scanResult}
              selectedImages={upload.selectedImages}
              toggleImage={upload.toggleImage}
              selectAll={upload.selectAll}
              selectNone={upload.selectNone}
              createDevices={upload.createDevices}
              setCreateDevices={upload.setCreateDevices}
              error={upload.error}
            />
          )}

          {/* Step 4: Importing */}
          {upload.step === 'importing' && (
            <ISOImportProgress
              overallProgress={upload.overallProgress}
              importProgress={upload.importProgress}
            />
          )}

          {/* Step 5: Complete */}
          {upload.step === 'complete' && (
            <div className="text-center py-12">
              <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center">
                <i className="fa-solid fa-check text-2xl text-emerald-500" />
              </div>
              <h3 className="text-lg font-bold text-stone-700 dark:text-stone-300">Import Complete!</h3>
              <p className="text-xs text-stone-500 mt-2">
                Images have been imported and are ready to use.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-stone-200 dark:border-stone-800 flex justify-between">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-bold text-stone-600 dark:text-stone-400 hover:text-stone-800 dark:hover:text-stone-200 transition-all"
          >
            {upload.step === 'complete' ? 'Close' : 'Cancel'}
          </button>

          <div className="flex gap-2">
            {upload.step === 'input' && upload.inputMode !== 'upload' && (
              <button
                onClick={upload.handleScan}
                disabled={!upload.isoPath.trim()}
                className="px-6 py-2 bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white rounded-lg text-xs font-bold transition-all"
              >
                <i className="fa-solid fa-magnifying-glass mr-2" />
                Scan ISO
              </button>
            )}

            {upload.step === 'input' && upload.inputMode === 'upload' && (
              <button
                onClick={upload.handleUpload}
                disabled={!upload.selectedFile}
                className="px-6 py-2 bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white rounded-lg text-xs font-bold transition-all"
              >
                <i className="fa-solid fa-upload mr-2" />
                Upload & Scan
              </button>
            )}

            {upload.step === 'review' && (
              <>
                <button
                  onClick={() => upload.setStep('input')}
                  className="px-4 py-2 glass-control text-stone-700 dark:text-stone-300 rounded-lg text-xs font-bold transition-all"
                >
                  <i className="fa-solid fa-arrow-left mr-2" />
                  Back
                </button>
                <button
                  onClick={() => upload.handleImport(onImportComplete)}
                  disabled={upload.selectedImages.size === 0}
                  className="px-6 py-2 bg-sage-600 hover:bg-sage-500 disabled:bg-stone-300 dark:disabled:bg-stone-700 text-white rounded-lg text-xs font-bold transition-all"
                >
                  <i className="fa-solid fa-download mr-2" />
                  Import {upload.selectedImages.size} Image{upload.selectedImages.size !== 1 ? 's' : ''}
                </button>
              </>
            )}

            {upload.step === 'complete' && (
              <button
                onClick={onClose}
                className="px-6 py-2 bg-sage-600 hover:bg-sage-500 text-white rounded-lg text-xs font-bold transition-all"
              >
                Done
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ISOImportModal;
