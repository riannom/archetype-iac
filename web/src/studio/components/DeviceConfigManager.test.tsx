import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import DeviceConfigManager from './DeviceConfigManager';
import { DeviceType } from '../types';

vi.mock('./DeviceConfigCard', () => ({
  default: ({ device, onSelect }: { device: { id: string; name: string }; onSelect: () => void }) => (
    <button onClick={onSelect}>{device.name}</button>
  ),
}));

vi.mock('./DeviceConfigPanel', () => ({
  default: ({ device }: { device: { id: string } }) => (
    <div data-testid="device-panel">{device.id}</div>
  ),
}));

vi.mock('./FilterChip', () => ({
  default: ({ label, onClick }: { label: string; onClick: () => void }) => (
    <button onClick={onClick}>{label}</button>
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

describe('DeviceConfigManager', () => {
  const deviceModels = [
    {
      id: 'router1',
      name: 'Router One',
      vendor: 'Acme',
      type: DeviceType.ROUTER,
      icon: 'fa-router',
      versions: ['1.0'],
      isActive: true,
    },
  ];

  it('adds a custom device and selects a model', () => {
    const onAddCustomDevice = vi.fn();
    const onRemoveCustomDevice = vi.fn();
    const onRefresh = vi.fn();

    render(
      <DeviceConfigManager
        deviceModels={deviceModels}
        customDevices={[]}
        imageLibrary={[]}
        onAddCustomDevice={onAddCustomDevice}
        onRemoveCustomDevice={onRemoveCustomDevice}
        onRefresh={onRefresh}
      />
    );

    fireEvent.click(screen.getByText('Add Custom Device'));

    fireEvent.change(screen.getByPlaceholderText('my-router'), {
      target: { value: 'custom-1' },
    });
    fireEvent.change(screen.getByPlaceholderText('My Router'), {
      target: { value: 'Custom 1' },
    });
    fireEvent.click(screen.getByText('Add Device'));

    expect(onAddCustomDevice).toHaveBeenCalledWith({ id: 'custom-1', label: 'Custom 1' });

    fireEvent.click(screen.getByText('Router One'));
    expect(screen.getByTestId('device-panel')).toHaveTextContent('router1');
  });
});
