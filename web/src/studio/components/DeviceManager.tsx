import React, { useMemo, useRef, useState } from 'react';
import { API_BASE_URL, apiRequest } from '../../api';
import { DeviceModel } from '../types';

interface ImageCatalogEntry {
  clab?: string;
  libvirt?: string;
  virtualbox?: string;
  caveats?: string[];
}

interface ImageLibraryEntry {
  id: string;
  kind: string;
  reference: string;
  device_id?: string | null;
  filename?: string;
  version?: string | null;
}

interface DeviceManagerProps {
  deviceModels: DeviceModel[];
  imageCatalog: Record<string, ImageCatalogEntry>;
  imageLibrary: ImageLibraryEntry[];
  onUploadImage: () => void;
  onUploadQcow2: () => void;
  onRefresh: () => void;
}

const DeviceManager: React.FC<DeviceManagerProps> = ({
  deviceModels,
  imageCatalog,
  imageLibrary,
  onUploadImage,
  onUploadQcow2,
  onRefresh,
}) => {
  const [selectedDevice, setSelectedDevice] = useState<DeviceModel | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [qcow2Progress, setQcow2Progress] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const qcow2InputRef = useRef<HTMLInputElement | null>(null);

  const libraryByDevice = useMemo(() => {
    const map = new Map<string, ImageLibraryEntry[]>();
    imageLibrary.forEach((image) => {
      if (!image.device_id) return;
      const entry = map.get(image.device_id) || [];
      entry.push(image);
      map.set(image.device_id, entry);
    });
    return map;
  }, [imageLibrary]);

  const assignedImages = selectedDevice ? libraryByDevice.get(selectedDevice.id) || [] : [];
  const unassignedImages = imageLibrary.filter((image) => !image.device_id);

  function openFilePicker() {
    fileInputRef.current?.click();
  }

  function openQcow2Picker() {
    qcow2InputRef.current?.click();
  }

  function uploadWithProgress(url: string, file: File, onProgress: (value: number | null) => void): Promise<any> {
    return new Promise((resolve, reject) => {
      const formData = new FormData();
      formData.append('file', file);
      const token = localStorage.getItem('token');
      const request = new XMLHttpRequest();
      request.open('POST', url);
      if (token) {
        request.setRequestHeader('Authorization', `Bearer ${token}`);
      }
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      request.onerror = () => reject(new Error('Upload failed'));
      request.onload = () => {
        if (request.status >= 200 && request.status < 300) {
          try {
            resolve(JSON.parse(request.responseText));
          } catch {
            resolve({});
          }
        } else {
          reject(new Error(request.responseText || 'Upload failed'));
        }
      };
      request.send(formData);
    });
  }

  async function uploadImage(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setUploadStatus(`Uploading ${file.name}...`);
      setUploadProgress(0);
      await uploadWithProgress(`${API_BASE_URL}/images/load`, file, setUploadProgress);
      setUploadStatus('Image loaded.');
      onUploadImage();
      onRefresh();
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'Upload failed');
    } finally {
      event.target.value = '';
      setUploadProgress(null);
    }
  }

  async function uploadQcow2(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setUploadStatus(`Uploading ${file.name}...`);
      setQcow2Progress(0);
      await uploadWithProgress(`${API_BASE_URL}/images/qcow2`, file, setQcow2Progress);
      setUploadStatus('QCOW2 uploaded.');
      onUploadQcow2();
      onRefresh();
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'Upload failed');
    } finally {
      event.target.value = '';
      setQcow2Progress(null);
    }
  }

  async function assignImage(imageId: string, deviceId: string | null) {
    await apiRequest(`/images/library/${encodeURIComponent(imageId)}`, {
      method: 'POST',
      body: JSON.stringify({ device_id: deviceId }),
    });
    onRefresh();
  }

  async function updateVersion(imageId: string, version: string) {
    await apiRequest(`/images/library/${encodeURIComponent(imageId)}`, {
      method: 'POST',
      body: JSON.stringify({ version }),
    });
    onRefresh();
  }

  return (
    <div className="flex-1 bg-slate-950 flex flex-col overflow-hidden animate-in fade-in duration-300">
      <div className="p-8 max-w-7xl mx-auto w-full flex-1 flex flex-col overflow-hidden">
        <header className="mb-6 flex flex-wrap justify-between items-end gap-4">
          <div>
            <h1 className="text-3xl font-black text-white tracking-tight">Image Management</h1>
            <p className="text-slate-400 text-sm mt-1">Load container images and assign them to device families.</p>
          </div>
          <div className="flex gap-3">
            <button onClick={openFilePicker} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-bold transition-all">
              <i className="fa-solid fa-cloud-arrow-up mr-2"></i> Upload image
            </button>
            <button onClick={openQcow2Picker} className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-lg border border-slate-700 text-xs font-bold transition-all">
              <i className="fa-solid fa-hard-drive mr-2"></i> Upload QCOW2
            </button>
            <input ref={fileInputRef} className="hidden" type="file" accept=".tar,.tgz,.tar.gz" onChange={uploadImage} />
            <input ref={qcow2InputRef} className="hidden" type="file" accept=".qcow2,.qcow" onChange={uploadQcow2} />
          </div>
        </header>

        {uploadStatus && <p className="text-xs text-slate-400 mb-4">{uploadStatus}</p>}
        {uploadProgress !== null && (
          <div className="mb-4">
            <div className="text-[10px] font-bold text-slate-500 uppercase mb-1">Image upload {uploadProgress}%</div>
            <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
              <div className="h-full bg-blue-500" style={{ width: `${uploadProgress}%` }} />
            </div>
          </div>
        )}
        {qcow2Progress !== null && (
          <div className="mb-4">
            <div className="text-[10px] font-bold text-slate-500 uppercase mb-1">QCOW2 upload {qcow2Progress}%</div>
            <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
              <div className="h-full bg-emerald-500" style={{ width: `${qcow2Progress}%` }} />
            </div>
          </div>
        )}

        <div className="grid grid-cols-12 gap-8 flex-1 overflow-hidden">
          <div className="col-span-4 flex flex-col gap-4 overflow-y-auto pr-2 custom-scrollbar">
            {deviceModels.map((model) => {
              const assignedCount = libraryByDevice.get(model.id)?.length || 0;
              return (
                <div
                  key={model.id}
                  onClick={() => setSelectedDevice(model)}
                  className={`p-4 rounded-xl border transition-all cursor-pointer group ${
                    selectedDevice?.id === model.id
                      ? 'bg-blue-600/10 border-blue-500 shadow-lg shadow-blue-900/20'
                      : 'bg-slate-900 border-slate-800 hover:border-slate-700'
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-4">
                      <div className={`w-12 h-12 rounded-lg flex items-center justify-center text-xl shadow-inner ${
                        selectedDevice?.id === model.id ? 'bg-blue-500 text-white' : 'bg-slate-800 text-slate-400 group-hover:text-blue-400 transition-colors'
                      }`}>
                        <i className={`fa-solid ${model.icon}`}></i>
                      </div>
                      <div>
                        <h3 className="font-bold text-white text-sm">{model.name}</h3>
                        <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">{model.vendor}</span>
                      </div>
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="text-[10px] text-slate-500 font-mono">{assignedCount} Images</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="col-span-8 bg-slate-900/50 border border-slate-800 rounded-2xl flex flex-col overflow-hidden">
            {selectedDevice ? (
              <>
                <div className="p-6 border-b border-slate-800 bg-slate-900/50 flex justify-between items-center">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded bg-blue-500/20 flex items-center justify-center text-blue-400 border border-blue-500/30">
                      <i className={`fa-solid ${selectedDevice.icon}`}></i>
                    </div>
                    <div>
                      <h2 className="text-xl font-bold text-white">{selectedDevice.name}</h2>
                      <p className="text-xs text-slate-500">Device ID: <span className="font-mono text-blue-400">{selectedDevice.id}</span></p>
                    </div>
                  </div>
                  <button onClick={onRefresh} className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-xs font-bold text-slate-200 rounded-lg">
                    <i className="fa-solid fa-rotate mr-2"></i> Refresh
                  </button>
                </div>

                <div className="flex-1 overflow-y-auto p-6 space-y-6">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="p-4 bg-slate-950/70 rounded-xl border border-slate-800">
                      <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Catalog</div>
                      {imageCatalog[selectedDevice.id] ? (
                        <div className="space-y-1 text-xs text-slate-300">
                          {imageCatalog[selectedDevice.id].clab && <div>clab: {imageCatalog[selectedDevice.id].clab}</div>}
                          {imageCatalog[selectedDevice.id].libvirt && <div>libvirt: {imageCatalog[selectedDevice.id].libvirt}</div>}
                          {imageCatalog[selectedDevice.id].virtualbox && <div>virtualbox: {imageCatalog[selectedDevice.id].virtualbox}</div>}
                          {imageCatalog[selectedDevice.id].caveats && imageCatalog[selectedDevice.id].caveats?.length ? (
                            <div className="text-[10px] text-slate-500">{imageCatalog[selectedDevice.id].caveats?.[0]}</div>
                          ) : null}
                        </div>
                      ) : (
                        <div className="text-xs text-slate-500 italic">No catalog entry.</div>
                      )}
                    </div>
                    <div className="p-4 bg-slate-950/70 rounded-xl border border-slate-800">
                      <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Assigned Images</div>
                      <div className="text-2xl font-black text-blue-400">{assignedImages.length}</div>
                      <div className="text-[10px] text-slate-500 uppercase font-bold">linked to this device</div>
                    </div>
                  </div>

                  <div>
                    <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Assigned images</div>
                    {assignedImages.length > 0 ? (
                      <div className="space-y-3">
                        {assignedImages.map((img) => (
                          <div key={img.id} className="flex flex-col gap-3 p-4 bg-slate-900 border border-slate-800 rounded-xl">
                            <div className="flex items-center justify-between">
                              <div>
                                <div className="text-sm font-bold text-white">{img.filename || img.reference}</div>
                                <div className="text-[11px] text-slate-500 font-mono mt-0.5">{img.kind}</div>
                              </div>
                              <button onClick={() => assignImage(img.id, null)} className="text-slate-500 hover:text-red-400 text-xs font-bold">
                                Unassign
                              </button>
                            </div>
                            <div className="flex items-center gap-4">
                              <div className="flex-1">
                                <label className="text-[9px] font-bold text-slate-500 uppercase">Version</label>
                                <input
                                  className="mt-1 w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200"
                                  defaultValue={img.version || ''}
                                  placeholder="Version"
                                  onBlur={(event) => updateVersion(img.id, event.target.value)}
                                />
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-xs text-slate-500 italic">No images assigned yet.</div>
                    )}
                  </div>

                  <div>
                    <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Unassigned images</div>
                    {unassignedImages.length > 0 ? (
                      <div className="space-y-3">
                        {unassignedImages.map((img) => (
                          <div key={img.id} className="flex items-center justify-between p-3 bg-slate-900 border border-slate-800 rounded-xl">
                            <div>
                              <div className="text-sm font-bold text-white">{img.filename || img.reference}</div>
                              <div className="text-[11px] text-slate-500 font-mono">{img.kind}</div>
                            </div>
                            <button onClick={() => assignImage(img.id, selectedDevice.id)} className="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-xs font-bold text-white rounded">
                              Assign
                            </button>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-xs text-slate-500 italic">No unassigned images.</div>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-slate-600">
                <div className="w-24 h-24 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center mb-6 shadow-2xl">
                  <i className="fa-solid fa-microchip text-4xl opacity-20"></i>
                </div>
                <h3 className="text-lg font-bold text-slate-400">Select a device to manage</h3>
                <p className="text-sm max-w-xs text-center mt-2">Pick a device on the left to see its images and catalog metadata.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default DeviceManager;
