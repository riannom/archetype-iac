import React from 'react';

interface InterfaceSelectProps {
  /** Currently selected interface value */
  value: string;
  /** List of available interfaces to show in dropdown */
  availableInterfaces: string[];
  /** Callback when selection changes */
  onChange: (value: string) => void;
  /** Placeholder text when no selection */
  placeholder?: string;
  /** Additional CSS classes */
  className?: string;
  /** Whether the select is disabled */
  disabled?: boolean;
}

/**
 * Dropdown component for selecting network interfaces.
 *
 * Shows available (unused) interfaces as options. If the current value
 * is already set (even if "used"), it remains visible as the first option.
 */
const InterfaceSelect: React.FC<InterfaceSelectProps> = ({
  value,
  availableInterfaces,
  onChange,
  placeholder = 'Select interface',
  className = '',
  disabled = false,
}) => {
  // Build options list: include current value first (if set), then available ones
  const options = React.useMemo(() => {
    const opts: string[] = [];

    // Add current value if it exists and isn't already in available list
    if (value && !availableInterfaces.includes(value)) {
      opts.push(value);
    }

    // Add all available interfaces
    availableInterfaces.forEach((iface) => {
      if (!opts.includes(iface)) {
        opts.push(iface);
      }
    });

    return opts;
  }, [value, availableInterfaces]);

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={`w-full bg-stone-50 dark:bg-stone-900 border border-stone-300 dark:border-stone-700 rounded px-2 py-1 text-[11px] text-sage-700 dark:text-sage-300 focus:outline-none focus:border-sage-500 appearance-none cursor-pointer ${
        disabled ? 'opacity-50 cursor-not-allowed' : ''
      } ${className}`}
      style={{
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2378716c'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E")`,
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'right 6px center',
        backgroundSize: '14px',
        paddingRight: '24px',
      }}
    >
      {!value && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {options.map((iface) => (
        <option key={iface} value={iface}>
          {iface}
        </option>
      ))}
    </select>
  );
};

export default InterfaceSelect;
