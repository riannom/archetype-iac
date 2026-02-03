import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { apiRequest } from '../../api';
import { DeviceModel, DeviceConfig, ImageLibraryEntry } from '../types';
import VendorOptionsPanel from './VendorOptionsPanel';
import { formatSize } from '../../utils/format';

interface DeviceConfigPanelProps {
  device: DeviceModel;
  imageLibrary: ImageLibraryEntry[];
  onRefresh: () => void;
}

interface ConfigSectionProps {
  title: string;
  icon: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}

const ConfigSection: React.FC<ConfigSectionProps> = ({
  title,
  icon,
  children,
  defaultOpen = true,
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div className="border border-stone-200 dark:border-stone-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full px-4 py-3 flex items-center justify-between bg-stone-100/50 dark:bg-stone-900/50 hover:bg-stone-100 dark:hover:bg-stone-900 transition-colors"
      >
        <div className="flex items-center gap-2">
          <i className={`fa-solid ${icon} text-xs text-stone-500`}></i>
          <span className="text-xs font-bold text-stone-700 dark:text-stone-300 uppercase tracking-wider">
            {title}
          </span>
        </div>
        <i className={`fa-solid fa-chevron-${isOpen ? 'up' : 'down'} text-xs text-stone-400`}></i>
      </button>
      {isOpen && (
        <div className="p-4 bg-white dark:bg-stone-950 border-t border-stone-200 dark:border-stone-800">
          {children}
        </div>
      )}
    </div>
  );
};

interface ConfigFieldProps {
  label: string;
  value: string | number | undefined;
  unit?: string;
  isOverridden?: boolean;
  readOnly?: boolean;
  type?: 'text' | 'number';
  onChange?: (value: string | number) => void;
}

const ConfigField: React.FC<ConfigFieldProps> = ({
  label,
  value,
  unit,
  isOverridden = false,
  readOnly = false,
  type = 'text',
  onChange,
}) => {
  const displayValue = value !== undefined && value !== null ? String(value) : '-';
  const inputValue = value !== undefined && value !== null ? value : '';

  return (
    <div className="flex items-center justify-between py-2 border-b border-stone-100 dark:border-stone-900 last:border-0">
      <div className="flex items-center gap-2">
        <span className="text-xs text-stone-600 dark:text-stone-400">{label}</span>
        {isOverridden && (
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500" title="Custom override"></span>
        )}
      </div>
      {readOnly ? (
        <div className="flex items-center gap-1">
          <span className="text-xs font-mono text-stone-700 dark:text-stone-300">
            {displayValue}
          </span>
          {unit && <span className="text-[10px] text-stone-400">{unit}</span>}
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <input
            type={type}
            value={inputValue}
            onChange={(e) => onChange?.(type === 'number' ? Number(e.target.value) : e.target.value)}
            className="w-24 px-2 py-1 text-xs font-mono text-right text-stone-900 dark:text-stone-100 bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded focus:outline-none focus:ring-1 focus:ring-sage-500"
          />
          {unit && <span className="text-[10px] text-stone-400">{unit}</span>}
        </div>
      )}
    </div>
  );
};

const DeviceConfigPanel: React.FC<DeviceConfigPanelProps> = ({
  device,
  imageLibrary,
  onRefresh,
}) => {
  const [config, setConfig] = useState<DeviceConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasChanges, setHasChanges] = useState(false);

  // Local edit state
  const [editValues, setEditValues] = useState<Record<string, unknown>>({});

  // Filter images assigned to this device
  const deviceImages = useMemo(() => {
    return imageLibrary.filter((img) => img.device_id === device.id);
  }, [imageLibrary, device.id]);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiRequest<DeviceConfig>(`/vendors/${device.id}/config`);
      setConfig(data);
      setEditValues({});
      setHasChanges(false);
    } catch (err) {
      console.error('Failed to load device config:', err);
      setError(err instanceof Error ? err.message : 'Failed to load configuration');
    } finally {
      setLoading(false);
    }
  }, [device.id]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const handleFieldChange = (field: string, value: unknown) => {
    setEditValues((prev) => ({ ...prev, [field]: value }));
    setHasChanges(true);
  };

  const handleVendorOptionChange = (key: string, value: unknown) => {
    setEditValues((prev) => {
      const currentVendorOptions = (prev.vendorOptions as Record<string, unknown>) || {};
      return {
        ...prev,
        vendorOptions: { ...currentVendorOptions, [key]: value },
      };
    });
    setHasChanges(true);
  };

  const handleSave = async () => {
    if (!hasChanges) return;
    setSaving(true);
    setError(null);
    try {
      await apiRequest(`/vendors/${device.id}/config`, {
        method: 'PUT',
        body: JSON.stringify(editValues),
      });
      await loadConfig();
      onRefresh();
    } catch (err) {
      console.error('Failed to save config:', err);
      setError(err instanceof Error ? err.message : 'Failed to save configuration');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!confirm('Reset this device to default configuration? This will remove all custom overrides.')) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await apiRequest(`/vendors/${device.id}/config`, {
        method: 'DELETE',
      });
      await loadConfig();
      onRefresh();
    } catch (err) {
      console.error('Failed to reset config:', err);
      setError(err instanceof Error ? err.message : 'Failed to reset configuration');
    } finally {
      setSaving(false);
    }
  };

  const getEffectiveValue = (field: string): string | number | undefined => {
    if (field in editValues) return editValues[field] as string | number | undefined;
    return config?.effective?.[field] as string | number | undefined;
  };

  const isFieldOverridden = (field: string): boolean => {
    if (field in editValues) return true;
    return field in (config?.overrides || {});
  };

  const hasOverrides = Object.keys(config?.overrides || {}).length > 0 || hasChanges;

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <i className="fa-solid fa-spinner fa-spin text-stone-400 text-xl"></i>
        <span className="ml-2 text-stone-500 text-sm">Loading configuration...</span>
      </div>
    );
  }

  if (error && !config) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <i className="fa-solid fa-exclamation-triangle text-2xl text-red-400 mb-3"></i>
          <p className="text-sm text-stone-600 dark:text-stone-400">{error}</p>
          <button
            onClick={loadConfig}
            className="mt-3 px-4 py-2 text-xs font-bold bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 rounded-lg transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const base = config?.base || {};
  const effective = config?.effective || {};

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-stone-200 dark:border-stone-800 bg-white/50 dark:bg-stone-900/50">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-lg bg-sage-600 text-white flex items-center justify-center">
              <i className={`fa-solid ${device.icon || 'fa-microchip'} text-lg`}></i>
            </div>
            <div>
              <h2 className="text-lg font-bold text-stone-900 dark:text-white">{device.name}</h2>
              <div className="flex flex-col gap-0.5 mt-0.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-stone-500">{device.vendor}</span>
                  {(base.isBuiltIn as boolean) && (
                    <span className="px-1.5 py-0.5 text-[8px] font-bold uppercase bg-stone-100 dark:bg-stone-800 text-stone-500 rounded">
                      Built-in
                    </span>
                  )}
                </div>
                <span className="text-[11px] text-stone-400 font-mono">{device.id}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {hasOverrides && (
              <button
                onClick={handleReset}
                disabled={saving}
                className="px-3 py-2 text-xs font-bold text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors disabled:opacity-50"
              >
                <i className="fa-solid fa-rotate-left mr-1.5"></i>
                Reset to Defaults
              </button>
            )}
            <button
              onClick={handleSave}
              disabled={!hasChanges || saving}
              className={`px-4 py-2 text-xs font-bold rounded-lg transition-all ${
                hasChanges
                  ? 'bg-sage-600 hover:bg-sage-500 text-white shadow-sm'
                  : 'bg-stone-100 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
              }`}
            >
              {saving ? (
                <>
                  <i className="fa-solid fa-spinner fa-spin mr-1.5"></i>
                  Saving...
                </>
              ) : (
                <>
                  <i className="fa-solid fa-save mr-1.5"></i>
                  Save Changes
                </>
              )}
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-3 p-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded text-xs text-red-600 dark:text-red-400">
            <i className="fa-solid fa-exclamation-triangle mr-1.5"></i>
            {error}
          </div>
        )}
      </div>

      {/* Config sections */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar">
        {/* Port Configuration */}
        <ConfigSection title="Port Configuration" icon="fa-ethernet">
          <ConfigField
            label="Interface Naming"
            value={getEffectiveValue('portNaming')}
            isOverridden={isFieldOverridden('portNaming')}
            onChange={(v) => handleFieldChange('portNaming', v)}
          />
          <ConfigField
            label="Start Index"
            value={getEffectiveValue('portStartIndex')}
            isOverridden={isFieldOverridden('portStartIndex')}
            type="number"
            onChange={(v) => handleFieldChange('portStartIndex', v)}
          />
          <ConfigField
            label="Max Ports"
            value={getEffectiveValue('maxPorts')}
            isOverridden={isFieldOverridden('maxPorts')}
            type="number"
            onChange={(v) => handleFieldChange('maxPorts', v)}
          />
        </ConfigSection>

        {/* Resource Allocation */}
        <ConfigSection title="Resource Allocation" icon="fa-memory">
          <ConfigField
            label="Memory"
            value={getEffectiveValue('memory')}
            unit="MB"
            isOverridden={isFieldOverridden('memory')}
            type="number"
            onChange={(v) => handleFieldChange('memory', v)}
          />
          <ConfigField
            label="CPU Cores"
            value={getEffectiveValue('cpu')}
            isOverridden={isFieldOverridden('cpu')}
            type="number"
            onChange={(v) => handleFieldChange('cpu', v)}
          />
        </ConfigSection>

        {/* Boot & Readiness */}
        <ConfigSection title="Boot & Readiness" icon="fa-clock">
          <ConfigField
            label="Readiness Probe"
            value={String(effective.readinessProbe || 'none')}
            readOnly
          />
          <ConfigField
            label="Readiness Pattern"
            value={String(effective.readinessPattern || '(none)')}
            readOnly
          />
          <ConfigField
            label="Readiness Timeout"
            value={getEffectiveValue('readinessTimeout')}
            unit="sec"
            isOverridden={isFieldOverridden('readinessTimeout')}
            type="number"
            onChange={(v) => handleFieldChange('readinessTimeout', v)}
          />
        </ConfigSection>

        {/* Vendor-Specific Options */}
        {(() => {
          const vendorOpts = effective.vendorOptions as Record<string, unknown> | undefined;
          if (!vendorOpts || Object.keys(vendorOpts).length === 0) return null;
          return (
            <ConfigSection title="Vendor-Specific Options" icon="fa-cog">
              <VendorOptionsPanel
                deviceId={device.id}
                vendorName={device.vendor}
                options={vendorOpts}
                baseOptions={(base.vendorOptions as Record<string, unknown>) || {}}
                overriddenOptions={(editValues.vendorOptions as Record<string, unknown>) || {}}
                onChange={handleVendorOptionChange}
              />
            </ConfigSection>
          );
        })()}

        {/* Documentation & Info */}
        <ConfigSection title="Documentation & Info" icon="fa-info-circle" defaultOpen={false}>
          <ConfigField
            label="Console Shell"
            value={String(effective.consoleShell || '-')}
            readOnly
          />
          <ConfigField
            label="Container Kind"
            value={String(effective.kind || '-')}
            readOnly
          />
          {typeof effective.documentationUrl === 'string' && effective.documentationUrl && (
            <div className="flex items-center justify-between py-2 border-b border-stone-100 dark:border-stone-900 last:border-0">
              <span className="text-xs text-stone-600 dark:text-stone-400">Documentation</span>
              <a
                href={effective.documentationUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-sage-600 dark:text-sage-400 hover:underline flex items-center gap-1"
              >
                View Docs
                <i className="fa-solid fa-external-link text-[10px]"></i>
              </a>
            </div>
          )}
          {typeof effective.notes === 'string' && effective.notes && (
            <div className="pt-2">
              <span className="text-[10px] text-stone-500 uppercase tracking-wider font-bold">Notes</span>
              <p className="mt-1 text-xs text-stone-600 dark:text-stone-400 italic">
                {effective.notes}
              </p>
            </div>
          )}
          <div className="flex flex-wrap gap-2 pt-3">
            {device.licenseRequired && (
              <span className="px-2 py-1 text-[10px] font-bold uppercase bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 rounded">
                License Required
              </span>
            )}
            {device.requiresImage && (
              <span className="px-2 py-1 text-[10px] font-bold uppercase bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded">
                Image Required
              </span>
            )}
            {device.tags?.map((tag) => (
              <span
                key={tag}
                className="px-2 py-1 text-[10px] font-medium bg-stone-100 dark:bg-stone-800 text-stone-500 rounded"
              >
                {tag}
              </span>
            ))}
          </div>
        </ConfigSection>

        {/* Associated Images */}
        <ConfigSection title="Associated Images" icon="fa-images" defaultOpen={false}>
          {deviceImages.length === 0 ? (
            <p className="text-xs text-stone-500 dark:text-stone-400 italic">
              No images assigned to this device
            </p>
          ) : (
            <div className="space-y-2">
              {deviceImages.map((img) => (
                <div
                  key={img.id}
                  className="flex items-center gap-3 p-2 bg-stone-50 dark:bg-stone-900/50 border border-stone-200 dark:border-stone-800 rounded-lg"
                >
                  {/* Kind icon */}
                  <div
                    className={`
                      w-8 h-8 rounded flex items-center justify-center shrink-0
                      ${img.kind === 'docker'
                        ? 'bg-blue-100 dark:bg-blue-900/30'
                        : 'bg-orange-100 dark:bg-orange-900/30'
                      }
                    `}
                  >
                    <i
                      className={`fa-brands ${img.kind === 'docker' ? 'fa-docker text-blue-500' : 'fa-hard-drive text-orange-500'}`}
                    />
                  </div>

                  {/* Image info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-stone-700 dark:text-stone-200 truncate">
                        {img.filename || img.reference}
                      </span>
                      {img.is_default && (
                        <span className="px-1.5 py-0.5 text-[8px] font-bold uppercase bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 rounded">
                          DEFAULT
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5 text-[10px] text-stone-400">
                      <span className="uppercase font-bold">{img.kind}</span>
                      {img.version && (
                        <>
                          <span>·</span>
                          <span>{img.version}</span>
                        </>
                      )}
                      {img.size_bytes && (
                        <>
                          <span>·</span>
                          <span>{formatSize(img.size_bytes)}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ConfigSection>
      </div>
    </div>
  );
};

export default DeviceConfigPanel;
