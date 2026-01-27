import React from 'react';

interface VendorOptionsPanelProps {
  deviceId: string;
  vendorName: string;
  options: Record<string, unknown>;
  baseOptions: Record<string, unknown>;
  overriddenOptions: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

interface ToggleOptionProps {
  label: string;
  description: string;
  value: boolean;
  isOverridden: boolean;
  onChange: (value: boolean) => void;
}

const ToggleOption: React.FC<ToggleOptionProps> = ({
  label,
  description,
  value,
  isOverridden,
  onChange,
}) => {
  return (
    <div className="flex items-start justify-between py-3 border-b border-stone-100 dark:border-stone-900 last:border-0">
      <div className="flex-1 pr-4">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-stone-700 dark:text-stone-300">{label}</span>
          {isOverridden && (
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500" title="Custom override"></span>
          )}
        </div>
        <p className="text-[10px] text-stone-500 dark:text-stone-400 mt-0.5">{description}</p>
      </div>
      <button
        onClick={() => onChange(!value)}
        className={`relative w-10 h-5 rounded-full transition-colors ${
          value
            ? 'bg-sage-600'
            : 'bg-stone-300 dark:bg-stone-700'
        }`}
      >
        <span
          className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
            value ? 'left-5' : 'left-0.5'
          }`}
        />
      </button>
    </div>
  );
};

/**
 * Renders vendor-specific configuration options based on the device type.
 * Each vendor may have different options that can be configured.
 */
const VendorOptionsPanel: React.FC<VendorOptionsPanelProps> = ({
  deviceId,
  vendorName,
  options,
  baseOptions,
  overriddenOptions,
  onChange,
}) => {
  const isOverridden = (key: string): boolean => {
    return key in overriddenOptions;
  };

  // Arista cEOS options
  if (deviceId === 'eos' || deviceId === 'ceos' || deviceId.includes('arista')) {
    return (
      <div>
        <ToggleOption
          label="Zero Touch Provisioning Cancel"
          description="Automatically cancel ZTP on boot to prevent boot delays in isolated lab environments"
          value={options.zerotouchCancel as boolean ?? true}
          isOverridden={isOverridden('zerotouchCancel')}
          onChange={(v) => onChange('zerotouchCancel', v)}
        />
      </div>
    );
  }

  // Nokia SR Linux options
  if (deviceId === 'srlinux' || deviceId === 'nokia_srlinux' || vendorName === 'Nokia') {
    return (
      <div>
        <ToggleOption
          label="gNMI Interface"
          description="Enable gNMI management interface for programmatic configuration"
          value={options.gnmiEnabled as boolean ?? true}
          isOverridden={isOverridden('gnmiEnabled')}
          onChange={(v) => onChange('gnmiEnabled', v)}
        />
      </div>
    );
  }

  // Generic options display for other vendors
  if (Object.keys(options).length > 0) {
    return (
      <div className="space-y-2">
        {Object.entries(options).map(([key, value]) => {
          if (typeof value === 'boolean') {
            return (
              <ToggleOption
                key={key}
                label={formatOptionLabel(key)}
                description={`Configure ${key} setting`}
                value={value}
                isOverridden={isOverridden(key)}
                onChange={(v) => onChange(key, v)}
              />
            );
          }
          return (
            <div key={key} className="flex items-center justify-between py-2 border-b border-stone-100 dark:border-stone-900 last:border-0">
              <div className="flex items-center gap-2">
                <span className="text-xs text-stone-600 dark:text-stone-400">{formatOptionLabel(key)}</span>
                {isOverridden(key) && (
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-500" title="Custom override"></span>
                )}
              </div>
              <span className="text-xs font-mono text-stone-700 dark:text-stone-300">
                {String(value)}
              </span>
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <p className="text-xs text-stone-500 dark:text-stone-400 italic">
      No vendor-specific options available for this device.
    </p>
  );
};

/**
 * Format camelCase or snake_case option keys to human-readable labels
 */
function formatOptionLabel(key: string): string {
  return key
    .replace(/([A-Z])/g, ' $1')
    .replace(/_/g, ' ')
    .replace(/^\s/, '')
    .split(' ')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

export default VendorOptionsPanel;
