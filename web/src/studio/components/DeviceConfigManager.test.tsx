import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, act } from '@testing-library/react';
import DeviceConfigManager from './DeviceConfigManager';
import { DeviceType } from '../types';

vi.mock('./DeviceConfigCard', () => ({
  default: ({ device, onSelect, isCustom, isRecentlyAdded }: { device: { id: string; name: string }; onSelect: () => void; isCustom?: boolean; isRecentlyAdded?: boolean }) => (
    <button onClick={onSelect} data-custom={isCustom} data-recently-added={isRecentlyAdded}>
      {device.name}
    </button>
  ),
}));

vi.mock('./DeviceConfigPanel', () => ({
  default: ({ device }: { device: { id: string } }) => (
    <div data-testid="device-panel">{device.id}</div>
  ),
}));

vi.mock('./FilterChip', () => ({
  default: ({ label, onClick, isActive }: { label: string; onClick: () => void; isActive: boolean }) => (
    <button onClick={onClick} data-active={isActive} data-testid={`filter-${label}`}>
      {label}
    </button>
  ),
}));

const toggleSet = (set: Set<string>, value: string) => {
  const next = new Set(set);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
};

vi.mock('../hooks/usePersistedState', () => ({
  usePersistedState: (_key: string, initial: string) => [initial, vi.fn()],
  usePersistedSet: (_key: string) => {
    let set = new Set<string>();
    const toggle = (value: string) => {
      set = toggleSet(set, value);
    };
    const clear = () => {
      set = new Set();
    };
    return [set, toggle, clear] as const;
  },
}));

// ─── Factories ─────────────────────────────────────────────────────

function makeDevice(overrides: Partial<any> = {}) {
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

function defaultProps(overrides: Partial<any> = {}) {
  return {
    deviceModels: [makeDevice()],
    customDevices: [] as { id: string; label: string }[],
    imageLibrary: [],
    onAddCustomDevice: vi.fn(),
    onRemoveCustomDevice: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  };
}

// ─── Tests ─────────────────────────────────────────────────────────

describe('DeviceConfigManager', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('adds a custom device and selects a model', () => {
    const props = defaultProps();

    render(<DeviceConfigManager {...props} />);

    // Open modal — find the header button specifically
    const headerButtons = screen.getAllByText('Add Custom Device');
    fireEvent.click(headerButtons[0]);

    fireEvent.change(screen.getByPlaceholderText('my-router'), {
      target: { value: 'custom-1' },
    });
    fireEvent.change(screen.getByPlaceholderText('My Router'), {
      target: { value: 'Custom 1' },
    });
    fireEvent.click(screen.getByText('Add Device'));

    expect(props.onAddCustomDevice).toHaveBeenCalledWith({ id: 'custom-1', label: 'Custom 1' });

    fireEvent.click(screen.getByText('Router One'));
    expect(screen.getByTestId('device-panel')).toHaveTextContent('router1');
  });

  it('renders the header and device count', () => {
    render(<DeviceConfigManager {...defaultProps()} />);
    expect(screen.getByText('Device Configuration')).toBeInTheDocument();
    expect(screen.getByText(/1 of 1 devices/)).toBeInTheDocument();
  });

  it('filters devices by search query matching name', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'ceos', name: 'Arista cEOS', vendor: 'Arista' }),
        makeDevice({ id: 'srl', name: 'Nokia SR Linux', vendor: 'Nokia' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);

    const searchInput = screen.getByPlaceholderText('Search devices...');
    fireEvent.change(searchInput, { target: { value: 'Arista' } });

    expect(screen.getByText('Arista cEOS')).toBeInTheDocument();
    expect(screen.queryByText('Nokia SR Linux')).not.toBeInTheDocument();
  });

  it('filters devices by search query matching ID', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'ceos', name: 'Arista cEOS' }),
        makeDevice({ id: 'srl', name: 'Nokia SR Linux' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);

    fireEvent.change(screen.getByPlaceholderText('Search devices...'), { target: { value: 'srl' } });

    expect(screen.getByText('Nokia SR Linux')).toBeInTheDocument();
    expect(screen.queryByText('Arista cEOS')).not.toBeInTheDocument();
  });

  it('shows empty state when all devices are filtered out', () => {
    render(<DeviceConfigManager {...defaultProps()} />);
    fireEvent.change(screen.getByPlaceholderText('Search devices...'), { target: { value: 'nonexistent' } });
    expect(screen.getByText('No devices match your filters')).toBeInTheDocument();
  });

  it('shows "Select a device" placeholder when no device is selected', () => {
    render(<DeviceConfigManager {...defaultProps()} />);
    expect(screen.getByText('Select a device')).toBeInTheDocument();
  });

  it('renders vendor filter chips', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'ceos', name: 'cEOS', vendor: 'Arista' }),
        makeDevice({ id: 'srl', name: 'SR Linux', vendor: 'Nokia' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);
    expect(screen.getByTestId('filter-Arista')).toBeInTheDocument();
    expect(screen.getByTestId('filter-Nokia')).toBeInTheDocument();
  });

  it('renders type filter chips', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'r1', name: 'Router', type: DeviceType.ROUTER }),
        makeDevice({ id: 's1', name: 'Switch', type: DeviceType.SWITCH }),
      ],
    });
    render(<DeviceConfigManager {...props} />);
    expect(screen.getByTestId(`filter-${DeviceType.ROUTER}`)).toBeInTheDocument();
    expect(screen.getByTestId(`filter-${DeviceType.SWITCH}`)).toBeInTheDocument();
  });

  it('shows delete confirmation dialog for custom devices', () => {
    const customDevice = { id: 'custom-1', label: 'My Custom' };
    const props = defaultProps({
      deviceModels: [makeDevice({ id: 'custom-1', name: 'My Custom', vendor: 'Custom' })],
      customDevices: [customDevice],
    });
    render(<DeviceConfigManager {...props} />);

    const deleteBtn = screen.getByTitle('Delete custom device');
    fireEvent.click(deleteBtn);

    expect(screen.getByText('Delete Custom Device')).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to delete/)).toBeInTheDocument();
    expect(screen.getByText('"My Custom"')).toBeInTheDocument();
  });

  it('confirms delete of custom device', () => {
    const customDevice = { id: 'custom-1', label: 'My Custom' };
    const props = defaultProps({
      deviceModels: [makeDevice({ id: 'custom-1', name: 'My Custom', vendor: 'Custom' })],
      customDevices: [customDevice],
    });
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getByTitle('Delete custom device'));
    const deleteButtons = screen.getAllByText('Delete');
    const confirmDeleteBtn = deleteButtons[deleteButtons.length - 1];
    fireEvent.click(confirmDeleteBtn);

    expect(props.onRemoveCustomDevice).toHaveBeenCalledWith('custom-1');
  });

  it('cancels delete confirmation dialog', () => {
    const customDevice = { id: 'custom-1', label: 'My Custom' };
    const props = defaultProps({
      deviceModels: [makeDevice({ id: 'custom-1', name: 'My Custom', vendor: 'Custom' })],
      customDevices: [customDevice],
    });
    render(<DeviceConfigManager {...props} />);

    fireEvent.click(screen.getByTitle('Delete custom device'));
    expect(screen.getByText('Delete Custom Device')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Cancel'));
    expect(screen.queryByText('Delete Custom Device')).not.toBeInTheDocument();
  });

  it('opens add custom device modal and cancels it', () => {
    render(<DeviceConfigManager {...defaultProps()} />);
    const headerButtons = screen.getAllByText('Add Custom Device');
    fireEvent.click(headerButtons[0]);

    // Modal opened — should see Device ID input
    expect(screen.getByPlaceholderText('my-router')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Cancel'));
    // Modal should be closed
    expect(screen.queryByPlaceholderText('my-router')).not.toBeInTheDocument();
  });

  it('disables Add Device button when ID is empty', () => {
    render(<DeviceConfigManager {...defaultProps()} />);
    const headerButtons = screen.getAllByText('Add Custom Device');
    fireEvent.click(headerButtons[0]);
    expect(screen.getByText('Add Device').closest('button')).toBeDisabled();
  });

  it('shows device count correctly with multiple devices', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'r1', name: 'Router 1' }),
        makeDevice({ id: 'r2', name: 'Router 2' }),
        makeDevice({ id: 'r3', name: 'Router 3' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);
    expect(screen.getByText(/3 of 3 devices/)).toBeInTheDocument();
  });

  it('shows filtered device count when search is active', () => {
    const props = defaultProps({
      deviceModels: [
        makeDevice({ id: 'ceos', name: 'Arista cEOS', vendor: 'Arista' }),
        makeDevice({ id: 'srl', name: 'Nokia SR Linux', vendor: 'Nokia' }),
        makeDevice({ id: 'csr', name: 'Cisco CSR', vendor: 'Cisco' }),
      ],
    });
    render(<DeviceConfigManager {...props} />);
    fireEvent.change(screen.getByPlaceholderText('Search devices...'), { target: { value: 'Arista' } });
    expect(screen.getByText(/1 of 3 devices/)).toBeInTheDocument();
  });
});
