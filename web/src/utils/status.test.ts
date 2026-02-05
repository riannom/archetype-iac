import {
  defaultThresholds,
  getResourceLevel,
  getResourceBgColor,
  getCpuColor,
  getMemoryColor,
  getStorageColor,
  getRuntimeStatusColor,
  getConnectionStatusColor,
  getConnectionStatusText,
  getRoleBadgeColor,
  getRoleLabel,
} from './status';

describe('status utils', () => {
  it('derives resource levels and colors', () => {
    expect(getResourceLevel(10, defaultThresholds.cpu)).toBe('normal');
    expect(getResourceLevel(70, defaultThresholds.cpu)).toBe('warning');
    expect(getResourceLevel(90, defaultThresholds.cpu)).toBe('danger');
    expect(getResourceBgColor('danger')).toBe('bg-red-500');
    expect(getCpuColor(20)).toBe('bg-sage-500');
    expect(getMemoryColor(80)).toBe('bg-amber-500');
    expect(getStorageColor(99)).toBe('bg-red-500');
  });

  it('maps runtime and connection states', () => {
    expect(getRuntimeStatusColor('running')).toContain('bg-green');
    expect(getConnectionStatusColor('offline')).toBe('bg-red-500');
    expect(getConnectionStatusText('degraded')).toBe('Degraded');
  });

  it('maps role badges', () => {
    expect(getRoleBadgeColor('controller')).toContain('purple');
    expect(getRoleLabel('agent+controller')).toBe('Agent + Controller');
  });
});
