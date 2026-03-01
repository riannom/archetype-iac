import React from 'react';
import { Modal } from '../../../components/ui/Modal';
import ISOImportModal from '../../../components/ISOImportModal';
import type { ISOImportLogEvent } from '../../../components/ISOImportModal';
import { Qcow2ConfirmState } from './deviceManagerTypes';

interface UploadControlsProps {
  uploadStatus: string | null;
  uploadProgress: number | null;
  qcow2Progress: number | null;
  isQcow2PostProcessing: boolean;
  uploadErrorCount: number;
  fileInputRef: React.MutableRefObject<HTMLInputElement | null>;
  qcow2InputRef: React.MutableRefObject<HTMLInputElement | null>;
  showISOModal: boolean;
  setShowISOModal: (show: boolean) => void;
  qcow2Confirm: Qcow2ConfirmState | null;
  setQcow2Confirm: React.Dispatch<React.SetStateAction<Qcow2ConfirmState | null>>;
  openFilePicker: () => void;
  openQcow2Picker: () => void;
  uploadImage: (event: React.ChangeEvent<HTMLInputElement>) => void;
  uploadQcow2: (event: React.ChangeEvent<HTMLInputElement>) => void;
  confirmQcow2Upload: () => void;
  cancelQcow2Confirm: () => void;
  handleIsoLogEvent: (event: ISOImportLogEvent) => void;
  onRefresh: () => void;
  onShowUploadLogs: () => void;
}

const UploadControls: React.FC<UploadControlsProps> = ({
  uploadStatus,
  uploadProgress,
  qcow2Progress,
  isQcow2PostProcessing,
  uploadErrorCount,
  fileInputRef,
  qcow2InputRef,
  showISOModal,
  setShowISOModal,
  qcow2Confirm,
  setQcow2Confirm,
  openFilePicker,
  openQcow2Picker,
  uploadImage,
  uploadQcow2,
  confirmQcow2Upload,
  cancelQcow2Confirm,
  handleIsoLogEvent,
  onRefresh,
  onShowUploadLogs,
}) => {
  return (
    <>
      {/* Header */}
      <header className="px-6 py-4 border-b border-stone-200 dark:border-stone-800 glass-surface">
        <div className="flex flex-wrap justify-between items-end gap-4">
          <div>
            <h1 className="text-2xl font-black text-stone-900 dark:text-white tracking-tight">
              Image Management
            </h1>
            <p className="text-stone-500 dark:text-stone-400 text-xs mt-1">
              Drag images onto devices to assign them. Drop zones appear when dragging.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <button
                onClick={openFilePicker}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-bold transition-all shadow-sm"
              >
                <i className="fa-solid fa-cloud-arrow-up mr-2"></i> Upload Docker
              </button>
              <button
                onClick={openQcow2Picker}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-bold transition-all shadow-sm"
              >
                <i className="fa-solid fa-hard-drive mr-2"></i> Upload QCOW2
              </button>
              <button
                onClick={() => setShowISOModal(true)}
                className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-xs font-bold transition-all shadow-sm"
              >
                <i className="fa-solid fa-compact-disc mr-2"></i> Import ISO
              </button>
            </div>
            <div className="ml-2 pl-3 border-l border-stone-200 dark:border-stone-700">
              <button
                onClick={onShowUploadLogs}
                className="px-4 py-2 glass-control text-stone-700 dark:text-white rounded-lg border border-stone-300 dark:border-stone-700 text-xs font-bold transition-all"
                title="View image upload and processing logs"
              >
                <i className="fa-solid fa-file-lines mr-2"></i> Logs
                {uploadErrorCount > 0 && (
                  <span className="ml-2 inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 text-[9px] font-black">
                    {uploadErrorCount}
                  </span>
                )}
              </button>
            </div>
            <input
              ref={fileInputRef}
              className="hidden"
              type="file"
              accept=".tar,.tgz,.tar.gz,.tar.xz,.txz"
              onChange={uploadImage}
            />
            <input
              ref={qcow2InputRef}
              className="hidden"
              type="file"
              accept=".qcow2,.qcow"
              onChange={uploadQcow2}
            />
          </div>
        </div>

        {/* Upload status */}
        {uploadStatus && (
          <p className="text-xs text-stone-500 dark:text-stone-400 mt-3">{uploadStatus}</p>
        )}
        {uploadProgress !== null && (
          <div className="mt-3">
            <div className="text-[10px] font-bold text-stone-500 uppercase mb-1">
              Image upload {uploadProgress}%
            </div>
            <div className="h-1.5 bg-stone-200 dark:bg-stone-800 rounded-full overflow-hidden">
              <div className="h-full bg-sage-500 transition-all" style={{ width: `${uploadProgress}%` }} />
            </div>
          </div>
        )}
        {qcow2Progress !== null && (
          <div className="mt-3">
            <div className="flex items-center gap-2 text-[10px] font-bold text-stone-500 uppercase mb-1">
              <span>
                {isQcow2PostProcessing
                  ? 'QCOW2 upload complete. Processing image...'
                  : `QCOW2 upload ${qcow2Progress}%`}
              </span>
              {isQcow2PostProcessing && (
                <i className="fa-solid fa-circle-notch fa-spin text-stone-400" />
              )}
            </div>
            <div className="h-1.5 bg-stone-200 dark:bg-stone-800 rounded-full overflow-hidden">
              <div
                className={`h-full bg-emerald-500 ${isQcow2PostProcessing ? 'animate-pulse' : 'transition-all'}`}
                style={{ width: isQcow2PostProcessing ? '100%' : `${qcow2Progress}%` }}
              />
            </div>
          </div>
        )}
      </header>

      {/* QCOW2 Confirmation Modal */}
      {qcow2Confirm && (
        <Modal
          isOpen={true}
          onClose={cancelQcow2Confirm}
          title="Confirm QCOW2 Image"
          size="md"
        >
          <div className="space-y-4">
            <div className="text-sm text-stone-600 dark:text-stone-300">
              <span className="font-medium">{qcow2Confirm.filename}</span>
              {qcow2Confirm.detection.size_bytes != null && (
                <span className="ml-2 text-stone-400">
                  ({(qcow2Confirm.detection.size_bytes / (1024 * 1024 * 1024)).toFixed(1)} GB)
                </span>
              )}
            </div>

            {qcow2Confirm.detection.confidence !== 'none' && (
              <div className={`text-xs px-2 py-1 rounded inline-block ${
                qcow2Confirm.detection.confidence === 'high'
                  ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                  : 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
              }`}>
                Detection confidence: {qcow2Confirm.detection.confidence}
              </div>
            )}

            <div>
              <label className="block text-xs font-medium text-stone-500 dark:text-stone-400 mb-1">
                Device Type
              </label>
              <input
                type="text"
                value={qcow2Confirm.deviceIdOverride}
                onChange={(e) => setQcow2Confirm((prev) => prev ? { ...prev, deviceIdOverride: e.target.value } : null)}
                placeholder="e.g. cisco_n9kv"
                className="w-full px-3 py-1.5 text-sm bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-500 dark:text-stone-400 mb-1">
                Version
              </label>
              <input
                type="text"
                value={qcow2Confirm.versionOverride}
                onChange={(e) => setQcow2Confirm((prev) => prev ? { ...prev, versionOverride: e.target.value } : null)}
                placeholder="e.g. 10.3.1"
                className="w-full px-3 py-1.5 text-sm bg-stone-50 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded-md"
              />
            </div>

            {Object.keys(qcow2Confirm.detection.suggested_metadata).length > 0 && (
              <div>
                <div className="text-xs font-medium text-stone-500 dark:text-stone-400 mb-1">
                  Vendor Defaults
                </div>
                <div className="grid grid-cols-2 gap-1 text-xs text-stone-500 dark:text-stone-400 bg-stone-50 dark:bg-stone-800 rounded-md p-2">
                  {Object.entries(qcow2Confirm.detection.suggested_metadata).map(([key, value]) => (
                    <React.Fragment key={key}>
                      <span className="font-mono">{key}</span>
                      <span>{String(value)}</span>
                    </React.Fragment>
                  ))}
                </div>
              </div>
            )}

            <label className="flex items-center gap-2 text-sm text-stone-600 dark:text-stone-300">
              <input
                type="checkbox"
                checked={qcow2Confirm.autoBuild}
                onChange={(e) => setQcow2Confirm((prev) => prev ? { ...prev, autoBuild: e.target.checked } : null)}
                className="rounded"
              />
              Auto-build Docker image (vrnetlab)
            </label>

            <div className="flex justify-end gap-2 pt-2 border-t border-stone-200 dark:border-stone-700">
              <button
                onClick={cancelQcow2Confirm}
                className="px-3 py-1.5 text-sm text-stone-600 dark:text-stone-300 hover:bg-stone-100 dark:hover:bg-stone-700 rounded-md"
              >
                Cancel
              </button>
              <button
                onClick={confirmQcow2Upload}
                className="px-3 py-1.5 text-sm bg-indigo-600 text-white hover:bg-indigo-700 rounded-md"
              >
                Confirm Import
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* ISO Import Modal */}
      <ISOImportModal
        isOpen={showISOModal}
        onClose={() => setShowISOModal(false)}
        onLogEvent={handleIsoLogEvent}
        onImportComplete={() => {
          onRefresh();
          setShowISOModal(false);
        }}
      />
    </>
  );
};

export default UploadControls;
