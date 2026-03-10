import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import DeviceConfigManager from './DeviceConfigManager';
import { DeviceType } from '../types';

// ─── Mocks ──────────────────────────────────────────────────────────

vi.mock('./DeviceConfigCard', () => ({
  default: ({
    device,
    onSelect,
    isSelected,
    isCustom,
    isRecentlyAdded,
  }: {
    device: { id: string; name: string };
    onSelect: () => void;
    isSelected?: boolean;
    isCustom?: boolean;
    isRecentlyAdded?: boolean;
  }) => (
    <button
      onClick={onSelect}
      data-testid={`device-card-${device.id}`}
      data-custom={isCustom}
      data-selected={isSelected}
      data-recently-added={isRecentlyAdded}
    >
      {device.name}
    </button>
  ),
}));

vi.mock('./DeviceConfigPanel', () => ({
  default: ({ device }: { device: { id: string; name: string } }) => (
    <div data-testid="device-panel">
      <span data-testid="panel-device-id">{device.id}</span>
      <span data-testid="panel-device-name">{device.name}</span>
    </div>
  ),
}));

vi.mock('./FilterChip', () => ({
  default: ({
    label,
    onClick,
    isActive,
  }: {
    label: string;
    onClick: () => void;
    isActive: boolean;
  }) => (
    <button
      onClick={onClick}
      data-active={isActive}
      data-testid={`filter-${label}`}
    >
      {label}
    </button>
  ),
}));

// Stateful mock for usePersistedSet so toggle/clear actually affect re-renders
let vendorSet: Set<string>;
let typeSet: Set<string>;
let vendorToggleFn: ReturnType<typeof vi.fn>;
let typeClearFn: ReturnType<typeof vi.fn>;
let vendorClearFn: ReturnType<typeof vi.fn>;
let typeToggleFn: ReturnType<typeof vi.fn>;

vi.mock('../hooks/usePersistedState', () => ({
  usePersistedState: (_key: string, initial: unknown) => [initial, vi.fn()],
  usePersistedSet: (key: string) => {
    // Return the appropriate set based on key
    if (key.includes('vendors')) {
      vendorToggleFn = vi.fn();
      vendorClearFn = vi.fn();
      return [vendorSet, vendorToggleFn, vendorClearFn] as const;
    }
    typeToggleFn = vi.fn();
    typeClearFn = vi.fn();
    return [typeSet, typeToggleFn, typeClearFn] as const;
  },
}));

// ─── Factories ──────────────────────────────────────────────────────

function makeDevice(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'router1',
    name: 'Router One',
    vendor: 'Acme',
    type: DeviceType.ROUTER,
    icon: 'fa-router',
    versions: ['1.0'],
    isActive: true,
    ...overrides,
  };
}

function defaultProps(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    deviceModels: [makeDevice()] as ReturnType<typeof makeDevice>[],
    customDevices: [] as { id: string; label: string }[],
    imageLibrary: [] as unknown[],
    onAddCustomDevice: vi.fn(),
    onRemoveCustomDevice: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  };
}

// ─── Tests ──────────────────────────────────────────────────────────

describe('DeviceConfigManager - interactions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vendorSet = new Set();
    typeSet = new Set();
  });

  // ── Add Modal ──────────────────────────────────────────────────

  it('does not call onAddCustomDevice when ID is whitespace-only', () => {
    const props = defaultProps();
    render(<DeviceConfigManager {...props} />);

    // Open modal
    fireEvent.click(screen.getAllByText('Add Custom Device')[0]);

    // Type whitespace-only ID
    fireEvent.change(screen.getByPlaceholderText('my-router'), {
      target: { value: '   ' },
    });

    // The Add Device button should be disabled (trim() is empty)
    const addBtn = screen.getByText('Add Device').closest('button')!;
    expect(addBtn).toBeDisabled();
  });

  it('uses device ID as label when display name is left empty', () => {
    const props = defaultProps();
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getAllByText('Add Custom Device')[0]);
    fireEvent.change(screen.getByPlaceholderText('my-router'), {
      target: { value: 'my-device' },
    });
    // Leave display name empty
    fireEvent.click(screen.getByText('Add Device'));

    expect(props.onAddCustomDevice).toHaveBeenCalledWith({
      id: 'my-device',
      label: 'my-device',
    });
  });

  it('submits custom device via Enter key in ID field', () => {
    const props = defaultProps();
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getAllByText('Add Custom Device')[0]);
    fireEvent.change(screen.getByPlaceholderText('my-router'), {
      target: { value: 'enter-device' },
    });
    fireEvent.keyDown(screen.getByPlaceholderText('my-router'), {
      key: 'Enter',
    });

    expect(props.onAddCustomDevice).toHaveBeenCalledWith({
      id: 'enter-device',
      label: 'enter-device',
    });
  });

  it('submits custom device via Enter key in label field', () => {
    const props = defaultProps();
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getAllByText('Add Custom Device')[0]);
    fireEvent.change(screen.getByPlaceholderText('my-router'), {
      target: { value: 'my-dev' },
    });
    fireEvent.change(screen.getByPlaceholderText('My Router'), {
      target: { value: 'My Device' },
    });
    fireEvent.keyDown(screen.getByPlaceholderText('My Router'), {
      key: 'Enter',
    });

    expect(props.onAddCustomDevice).toHaveBeenCalledWith({
      id: 'my-dev',
      label: 'My Device',
    });
  });

  it('closes add modal after successful submission', () => {
    const props = defaultProps();
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getAllByText('Add Custom Device')[0]);
    expect(screen.getByPlaceholderText('my-router')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('my-router'), {
      target: { value: 'new-dev' },
    });
    fireEvent.click(screen.getByText('Add Device'));

    // Modal should be gone
    expect(screen.queryByPlaceholderText('my-router')).not.toBeInTheDocument();
  });

  it('clears form fields after adding a custom device', () => {
    const props = defaultProps();
    render(<DeviceConfigManager {...props} />);

    // Add first device
    fireEvent.click(screen.getAllByText('Add Custom Device')[0]);
    fireEvent.change(screen.getByPlaceholderText('my-router'), {
      target: { value: 'dev-1' },
    });
    fireEvent.change(screen.getByPlaceholderText('My Router'), {
      target: { value: 'Device 1' },
    });
    fireEvent.click(screen.getByText('Add Device'));

    // Re-open modal — fields should be clear
    fireEvent.click(screen.getAllByText('Add Custom Device')[0]);
    expect(screen.getByPlaceholderText('my-router')).toHaveValue('');
    expect(screen.getByPlaceholderText('My Router')).toHaveValue('');
  });

  // ── Delete Confirmation ────────────────────────────────────────

  it('clears selected device when deleting the currently selected device', () => {
    const customDevice = { id: 'custom-1', label: 'My Custom' };
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'custom-1', name: 'My Custom', vendor: 'Custom' }),
        makeDevice({ id: 'regular-1', name: 'Regular Device' }),
      ],
      customDevices: [customDevice],
    });
    render(<DeviceConfigManager {...props} />);

    // Select the custom device
    fireEvent.click(screen.getByTestId('device-card-custom-1'));
    expect(screen.getByTestId('device-panel')).toBeInTheDocument();
    expect(screen.getByTestId('panel-device-id')).toHaveTextContent('custom-1');

    // Delete it
    fireEvent.click(screen.getByTitle('Delete custom device'));
    const deleteButtons = screen.getAllByText('Delete');
    fireEvent.click(deleteButtons[deleteButtons.length - 1]);

    // Panel should revert to placeholder
    expect(screen.queryByTestId('device-panel')).not.toBeInTheDocument();
    expect(screen.getByText('Select a device')).toBeInTheDocument();
  });

  it('shows the device ID in the delete confirmation dialog', () => {
    const customDevice = { id: 'xyz-router', label: 'XYZ Router' };
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'xyz-router', name: 'XYZ Router', vendor: 'Custom' }),
      ],
      customDevices: [customDevice],
    });
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getByTitle('Delete custom device'));

    // Should display both label and ID
    expect(screen.getByText('"XYZ Router"')).toBeInTheDocument();
    expect(screen.getByText('xyz-router')).toBeInTheDocument();
    expect(screen.getByText('This action cannot be undone')).toBeInTheDocument();
  });

  it('does not call onRemoveCustomDevice when cancel is clicked', () => {
    const customDevice = { id: 'c1', label: 'C1' };
    const props = defaultProps({
      deviceModels: [makeDevice({ id: 'c1', name: 'C1', vendor: 'V' })],
      customDevices: [customDevice],
    });
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getByTitle('Delete custom device'));
    fireEvent.click(screen.getByText('Cancel'));

    expect(props.onRemoveCustomDevice).not.toHaveBeenCalled();
  });

  // ── Search & Filtering ─────────────────────────────────────────

  it('filters devices by tag match', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'd1', name: 'Device A', tags: ['networking', 'core'] }),
        makeDevice({ id: 'd2', name: 'Device B', tags: ['edge'] }),
      ],
    });
    render(<DeviceConfigManager {...props} />);

    fireEvent.change(screen.getByPlaceholderText('Search devices...'), {
      target: { value: 'core' },
    });

    expect(screen.getByText('Device A')).toBeInTheDocument();
    expect(screen.queryByText('Device B')).not.toBeInTheDocument();
  });

  it('shows clear search button only when search has text', () => {
    render(<DeviceConfigManager {...defaultProps()} />);

    // Initially no clear button (the xmark icon button)
    const searchInput = screen.getByPlaceholderText('Search devices...');
    // fa-xmark button should not be present when search is empty
    expect(searchInput.parentElement!.querySelectorAll('button').length).toBe(0);

    fireEvent.change(searchInput, { target: { value: 'test' } });
    // Now the clear button should appear
    expect(searchInput.parentElement!.querySelectorAll('button').length).toBe(1);
  });

  it('clears search text when clear button is clicked', () => {
    render(<DeviceConfigManager {...defaultProps()} />);
    const searchInput = screen.getByPlaceholderText('Search devices...');

    fireEvent.change(searchInput, { target: { value: 'hello' } });
    expect(searchInput).toHaveValue('hello');

    const clearBtn = searchInput.parentElement!.querySelector('button')!;
    fireEvent.click(clearBtn);

    expect(searchInput).toHaveValue('');
  });

  it('shows "Clear" button when filters are active and clears all on click', () => {
    vendorSet = new Set(['Arista']);
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'ceos', name: 'cEOS', vendor: 'Arista' }),
        makeDevice({ id: 'srl', name: 'SR Linux', vendor: 'Nokia' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);

    // "Clear" button should be visible because vendorSet has a value
    const clearBtn = screen.getByText('Clear');
    fireEvent.click(clearBtn);

    // Both clear functions should have been called
    expect(vendorClearFn).toHaveBeenCalled();
    expect(typeClearFn).toHaveBeenCalled();
  });

  it('does not show "Clear" button when no filters are active', () => {
    render(<DeviceConfigManager {...defaultProps()} />);
    expect(screen.queryByText('Clear')).not.toBeInTheDocument();
  });

  // ── Refresh ────────────────────────────────────────────────────

  it('calls onRefresh when refresh button is clicked', () => {
    const props = defaultProps();
    render(<DeviceConfigManager {...props} />);

    // Find the refresh button (the one with fa-rotate icon, no text)
    const buttons = screen.getAllByRole('button');
    const refreshBtn = buttons.find(
      (btn) => btn.querySelector('.fa-rotate') !== null
    )!;
    fireEvent.click(refreshBtn);

    expect(props.onRefresh).toHaveBeenCalledTimes(1);
  });

  // ── Device Selection / Panel ───────────────────────────────────

  it('opens config panel when a device card is clicked', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'dev-a', name: 'Device Alpha' }),
        makeDevice({ id: 'dev-b', name: 'Device Beta' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);

    expect(screen.getByText('Select a device')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('device-card-dev-b'));

    expect(screen.queryByText('Select a device')).not.toBeInTheDocument();
    expect(screen.getByTestId('panel-device-id')).toHaveTextContent('dev-b');
    expect(screen.getByTestId('panel-device-name')).toHaveTextContent('Device Beta');
  });

  it('switches config panel when a different device is selected', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'dev-a', name: 'Device Alpha' }),
        makeDevice({ id: 'dev-b', name: 'Device Beta' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getByTestId('device-card-dev-a'));
    expect(screen.getByTestId('panel-device-id')).toHaveTextContent('dev-a');

    fireEvent.click(screen.getByTestId('device-card-dev-b'));
    expect(screen.getByTestId('panel-device-id')).toHaveTextContent('dev-b');
  });

  // ── Custom vs Regular Device Sections ──────────────────────────

  it('shows "Custom Devices" section header when custom devices exist', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'cust-1', name: 'Custom One', vendor: 'Custom' }),
        makeDevice({ id: 'reg-1', name: 'Regular One', vendor: 'Vendor' }),
      ],
      customDevices: [{ id: 'cust-1', label: 'Custom One' }],
    });
    render(<DeviceConfigManager {...props} />);

    expect(screen.getByText('Custom Devices')).toBeInTheDocument();
    expect(screen.getByText('Vendor Devices')).toBeInTheDocument();
  });

  it('does not show section headers when there are no custom devices', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'reg-1', name: 'Regular One' }),
        makeDevice({ id: 'reg-2', name: 'Regular Two' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);

    expect(screen.queryByText('Custom Devices')).not.toBeInTheDocument();
    expect(screen.queryByText('Vendor Devices')).not.toBeInTheDocument();
  });

  it('marks custom device cards with isCustom=true', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'cust-1', name: 'Custom One', vendor: 'V' }),
      ],
      customDevices: [{ id: 'cust-1', label: 'Custom One' }],
    });
    render(<DeviceConfigManager {...props} />);

    const card = screen.getByTestId('device-card-cust-1');
    expect(card.getAttribute('data-custom')).toBe('true');
  });

  it('shows custom device count in section header', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'c1', name: 'Custom 1', vendor: 'V' }),
        makeDevice({ id: 'c2', name: 'Custom 2', vendor: 'V' }),
        makeDevice({ id: 'r1', name: 'Regular 1', vendor: 'V' }),
      ],
      customDevices: [
        { id: 'c1', label: 'Custom 1' },
        { id: 'c2', label: 'Custom 2' },
      ],
    });
    render(<DeviceConfigManager {...props} />);

    expect(screen.getByText('(2)')).toBeInTheDocument();
    expect(screen.getByText('(1)')).toBeInTheDocument();
  });

  // ── Empty State ────────────────────────────────────────────────

  it('shows empty state when deviceModels is empty', () => {
    const props = defaultProps({ deviceModels: [] });
    render(<DeviceConfigManager {...props} />);

    expect(screen.getByText('No devices match your filters')).toBeInTheDocument();
    expect(screen.getByText(/0 of 0 devices/)).toBeInTheDocument();
  });

  // ── Search case insensitivity ──────────────────────────────────

  it('search is case-insensitive for device name', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'ceos', name: 'Arista cEOS' }),
        makeDevice({ id: 'srl', name: 'Nokia SR Linux' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);

    fireEvent.change(screen.getByPlaceholderText('Search devices...'), {
      target: { value: 'arista ceos' },
    });

    expect(screen.getByText('Arista cEOS')).toBeInTheDocument();
    expect(screen.queryByText('Nokia SR Linux')).not.toBeInTheDocument();
  });
});
