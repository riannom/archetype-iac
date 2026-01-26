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
  customDevices: { id: string; label: string }[];
  onAddCustomDevice: (device: { id: string; label: string }) => void;
  onRemoveCustomDevice: (deviceId: string) => void;
  onUploadImage: () => void;
  onUploadQcow2: () => void;
  onRefresh: () => void;
}

const DeviceManager: React.FC<DeviceManagerProps> = ({
  deviceModels,
  imageCatalog,
  imageLibrary,
  customDevices,
  onAddCustomDevice,
  onRemoveCustomDevice,
  onUploadImage,
  onUploadQcow2,
  onRefresh,
}) => {
  const [selectedDevice, setSelectedDevice] = useState<DeviceModel | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [qcow2Progress, setQcow2Progress] = useState<number | null>(null);
  const [customAssignments, setCustomAssignments] = useState<Record<string, string>>({});
  const [customDeviceId, setCustomDeviceId] = useState('');
  const [customDeviceLabel, setCustomDeviceLabel] = useState('');
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
      const timeout = window.setTimeout(() => {
        request.abort();
        reject(new Error('Upload timed out while processing the image. Large images may take several minutes.'));
      }, 10 * 60 * 1000);
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      request.onerror = () => {
        window.clearTimeout(timeout);
        reject(new Error('Upload failed'));
      };
      request.onload = () => {
        window.clearTimeout(timeout);
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
    let processingNoticeShown = false;
    try {
      setUploadStatus(`Uploading ${file.name}...`);
      setUploadProgress(0);
      const data = (await uploadWithProgress(`${API_BASE_URL}/images/load`, file, (value) => {
        setUploadProgress(value);
        if (value !== null && value >= 100 && !processingNoticeShown) {
          processingNoticeShown = true;
          setUploadStatus('Upload complete. Importing image (this may take a few minutes for large files)...');
        }
      })) as {
        output?: string;
        images?: string[];
      };
      if (data.images && data.images.length === 0) {
        setUploadStatus('Upload finished, but no images were detected.');
      } else {
        setUploadStatus(data.output || 'Image loaded.');
      }
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
    <div className="flex-1 bg-stone-950 flex flex-col overflow-hidden animate-in fade-in duration-300">
      <div className="p-8 max-w-7xl mx-auto w-full flex-1 flex flex-col overflow-hidden">
        <header className="mb-6 flex flex-wrap justify-between items-end gap-4">
          <div>
            <h1 className="text-3xl font-black text-white tracking-tight">Image Management</h1>
            <p className="text-stone-400 text-sm mt-1">Load container images and assign them to device families.</p>
          </div>
          <div className="flex gap-3">
            <button onClick={openFilePicker} className="px-4 py-2 bg-sage-600 hover:bg-sage-500 text-white rounded-lg text-xs font-bold transition-all">
              <i className="fa-solid fa-cloud-arrow-up mr-2"></i> Upload image
            </button>
            <button onClick={openQcow2Picker} className="px-4 py-2 bg-stone-800 hover:bg-stone-700 text-white rounded-lg border border-stone-700 text-xs font-bold transition-all">
              <i className="fa-solid fa-hard-drive mr-2"></i> Upload QCOW2
            </button>
            <input ref={fileInputRef} className="hidden" type="file" accept=".tar,.tgz,.tar.gz,.tar.xz,.txz" onChange={uploadImage} />
            <input ref={qcow2InputRef} className="hidden" type="file" accept=".qcow2,.qcow" onChange={uploadQcow2} />
          </div>
        </header>

        {uploadStatus && <p className="text-xs text-stone-400 mb-4">{uploadStatus}</p>}
        {uploadProgress !== null && (
          <div className="mb-4">
            <div className="text-[10px] font-bold text-stone-500 uppercase mb-1">Image upload {uploadProgress}%</div>
            <div className="h-1.5 bg-stone-800 rounded-full overflow-hidden">
              <div className="h-full bg-sage-500" style={{ width: `${uploadProgress}%` }} />
            </div>
          </div>
        )}
        {qcow2Progress !== null && (
          <div className="mb-4">
            <div className="text-[10px] font-bold text-stone-500 uppercase mb-1">QCOW2 upload {qcow2Progress}%</div>
            <div className="h-1.5 bg-stone-800 rounded-full overflow-hidden">
              <div className="h-full bg-emerald-500" style={{ width: `${qcow2Progress}%` }} />
            </div>
          </div>
        )}

        <div className="grid grid-cols-12 gap-8 flex-1 overflow-hidden">
          <div className="col-span-4 flex flex-col gap-4 overflow-y-auto pr-2 custom-scrollbar">
            <div className="bg-stone-900 border border-stone-800 rounded-xl p-4">
              <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest mb-2">Custom device</div>
              <div className="space-y-2">
                <input
                  className="w-full bg-stone-950 border border-stone-700 rounded px-3 py-2 text-xs text-stone-200"
                  placeholder="device-id (e.g. my-os)"
                  value={customDeviceId}
                  onChange={(event) => setCustomDeviceId(event.target.value)}
                />
                <input
                  className="w-full bg-stone-950 border border-stone-700 rounded px-3 py-2 text-xs text-stone-200"
                  placeholder="label (optional)"
                  value={customDeviceLabel}
                  onChange={(event) => setCustomDeviceLabel(event.target.value)}
                />
                <button
                  type="button"
                  onClick={() => {
                    if (!customDeviceId.trim()) return;
                    onAddCustomDevice({ id: customDeviceId.trim(), label: customDeviceLabel.trim() || customDeviceId.trim() });
                    setCustomDeviceId('');
                    setCustomDeviceLabel('');
                  }}
                  className="w-full bg-sage-600 hover:bg-sage-500 text-white text-xs font-bold rounded-lg py-2"
                >
                  Add device
                </button>
              </div>
              {customDevices.length > 0 && (
                <div className="mt-3 space-y-2">
                  {customDevices.map((device) => (
                    <div key={device.id} className="flex items-center justify-between text-[11px] text-stone-400">
                      <span className="font-mono">{device.id}</span>
                      <button onClick={() => onRemoveCustomDevice(device.id)} className="text-red-400 hover:text-red-300">Remove</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {deviceModels.map((model) => {
              const assignedCount = libraryByDevice.get(model.id)?.length || 0;
              return (
                <div
                  key={model.id}
                  onClick={() => setSelectedDevice(model)}
                  className={`p-4 rounded-xl border transition-all cursor-pointer group ${
                    selectedDevice?.id === model.id
                      ? 'bg-sage-600/10 border-sage-500 shadow-lg shadow-sage-900/20'
                      : 'bg-stone-900 border-stone-800 hover:border-stone-700'
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-4">
                      <div className={`w-12 h-12 rounded-lg flex items-center justify-center text-xl shadow-inner ${
                        selectedDevice?.id === model.id ? 'bg-sage-500 text-white' : 'bg-stone-800 text-stone-400 group-hover:text-sage-400 transition-colors'
                      }`}>
                        <i className={`fa-solid ${model.icon}`}></i>
                      </div>
                      <div>
                        <h3 className="font-bold text-white text-sm">{model.name}</h3>
                        <span className="text-[10px] uppercase font-bold text-stone-500 tracking-wider">{model.vendor}</span>
                      </div>
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="text-[10px] text-stone-500 font-mono">{assignedCount} Images</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="col-span-8 bg-stone-900/50 border border-stone-800 rounded-2xl flex flex-col overflow-hidden">
            {selectedDevice ? (
              <>
                <div className="p-6 border-b border-stone-800 bg-stone-900/50 flex justify-between items-center">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded bg-sage-500/20 flex items-center justify-center text-sage-400 border border-sage-500/30">
                      <i className={`fa-solid ${selectedDevice.icon}`}></i>
                    </div>
                    <div>
                      <h2 className="text-xl font-bold text-white">{selectedDevice.name}</h2>
                      <p className="text-xs text-stone-500">Device ID: <span className="font-mono text-sage-400">{selectedDevice.id}</span></p>
                    </div>
                  </div>
                  <button onClick={onRefresh} className="px-3 py-2 bg-stone-800 hover:bg-stone-700 text-xs font-bold text-stone-200 rounded-lg">
                    <i className="fa-solid fa-rotate mr-2"></i> Refresh
                  </button>
                </div>

                <div className="flex-1 overflow-y-auto p-6 space-y-6">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="p-4 bg-stone-950/70 rounded-xl border border-stone-800">
                      <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest mb-2">Catalog</div>
                      {imageCatalog[selectedDevice.id] ? (
                        <div className="space-y-1 text-xs text-stone-300">
                          {imageCatalog[selectedDevice.id].clab && <div>clab: {imageCatalog[selectedDevice.id].clab}</div>}
                          {imageCatalog[selectedDevice.id].libvirt && <div>libvirt: {imageCatalog[selectedDevice.id].libvirt}</div>}
                          {imageCatalog[selectedDevice.id].virtualbox && <div>virtualbox: {imageCatalog[selectedDevice.id].virtualbox}</div>}
                          {imageCatalog[selectedDevice.id].caveats && imageCatalog[selectedDevice.id].caveats?.length ? (
                            <div className="text-[10px] text-stone-500">{imageCatalog[selectedDevice.id].caveats?.[0]}</div>
                          ) : null}
                        </div>
                      ) : (
                        <div className="text-xs text-stone-500 italic">No catalog entry.</div>
                      )}
                    </div>
                    <div className="p-4 bg-stone-950/70 rounded-xl border border-stone-800">
                      <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest mb-2">Assigned Images</div>
                      <div className="text-2xl font-black text-sage-400">{assignedImages.length}</div>
                      <div className="text-[10px] text-stone-500 uppercase font-bold">linked to this device</div>
                    </div>
                  </div>

                  <div>
                    <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest mb-2">Assigned images</div>
                    {assignedImages.length > 0 ? (
                      <div className="space-y-3">
                        {assignedImages.map((img) => (
                          <div key={img.id} className="flex flex-col gap-3 p-4 bg-stone-900 border border-stone-800 rounded-xl">
                            <div className="flex items-center justify-between">
                              <div>
                                <div className="text-sm font-bold text-white">{img.filename || img.reference}</div>
                                <div className="text-[11px] text-stone-500 font-mono mt-0.5">{img.kind}</div>
                              </div>
                              <button onClick={() => assignImage(img.id, null)} className="text-stone-500 hover:text-red-400 text-xs font-bold">
                                Unassign
                              </button>
                            </div>
                            <div className="flex items-center gap-4">
                              <div className="flex-1">
                                <label className="text-[9px] font-bold text-stone-500 uppercase">Version</label>
                                <input
                                  className="mt-1 w-full bg-stone-950 border border-stone-700 rounded px-2 py-1 text-xs text-stone-200"
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
                      <div className="text-xs text-stone-500 italic">No images assigned yet.</div>
                    )}
                  </div>

                  <div>
                    <div className="text-[10px] font-bold text-stone-500 uppercase tracking-widest mb-2">Unassigned images</div>
                    {unassignedImages.length > 0 ? (
                      <div className="space-y-3">
                        {unassignedImages.map((img) => (
                          <div key={img.id} className="flex items-center justify-between p-3 bg-stone-900 border border-stone-800 rounded-xl">
                            <div>
                              <div className="text-sm font-bold text-white">{img.filename || img.reference}</div>
                              <div className="text-[11px] text-stone-500 font-mono">{img.kind}</div>
                            </div>
                            <div className="flex items-center gap-2">
                              <input
                                className="bg-stone-950 border border-stone-700 rounded px-2 py-1 text-[11px] text-stone-200 w-36"
                                placeholder="device-id"
                                value={customAssignments[img.id] || ''}
                                onChange={(event) =>
                                  setCustomAssignments((prev) => ({ ...prev, [img.id]: event.target.value }))
                                }
                              />
                              <button
                                onClick={() => assignImage(img.id, customAssignments[img.id] || selectedDevice?.id || null)}
                                className="px-3 py-1 bg-sage-600 hover:bg-sage-500 text-xs font-bold text-white rounded disabled:opacity-60"
                                disabled={!customAssignments[img.id] && !selectedDevice}
                              >
                                Assign
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-xs text-stone-500 italic">No unassigned images.</div>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-stone-600">
                <div className="w-24 h-24 rounded-full bg-stone-900 border border-stone-800 flex items-center justify-center mb-6 shadow-2xl">
                  <i className="fa-solid fa-microchip text-4xl opacity-20"></i>
                </div>
                <h3 className="text-lg font-bold text-stone-400">Select a device to manage</h3>
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
