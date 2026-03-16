import React from 'react';
import { Select } from '../../components/ui/Select';

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
    <Select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      size="sm"
      className={className}
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
    </Select>
  );
};

export default InterfaceSelect;
