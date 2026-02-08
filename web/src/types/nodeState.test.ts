import { describe, it, expect } from 'vitest';
import { mapActualToRuntime } from './nodeState';

describe('mapActualToRuntime', () => {
  it('maps "running" to "running"', () => {
    expect(mapActualToRuntime('running')).toBe('running');
  });

  it('maps "stopping" to "stopping"', () => {
    expect(mapActualToRuntime('stopping')).toBe('stopping');
  });

  it('maps "starting" to "booting"', () => {
    expect(mapActualToRuntime('starting')).toBe('booting');
  });

  it('maps "pending" with desired=running to "booting"', () => {
    expect(mapActualToRuntime('pending', 'running')).toBe('booting');
  });

  it('maps "pending" with desired=stopped to "stopped"', () => {
    expect(mapActualToRuntime('pending', 'stopped')).toBe('stopped');
  });

  it('maps "pending" with no desired state to "stopped"', () => {
    expect(mapActualToRuntime('pending')).toBe('stopped');
  });

  it('maps "error" with willRetry=false to "error"', () => {
    expect(mapActualToRuntime('error', 'running', false)).toBe('error');
  });

  it('maps "error" with willRetry=undefined to "error"', () => {
    expect(mapActualToRuntime('error', 'running')).toBe('error');
  });

  it('maps "error" with willRetry=true to "booting" (suppression)', () => {
    expect(mapActualToRuntime('error', 'running', true)).toBe('booting');
  });

  it('maps "stopped" to "stopped"', () => {
    expect(mapActualToRuntime('stopped')).toBe('stopped');
  });

  it('maps "exited" to "stopped"', () => {
    expect(mapActualToRuntime('exited')).toBe('stopped');
  });

  it('maps "undeployed" to null', () => {
    expect(mapActualToRuntime('undeployed')).toBeNull();
  });

  it('maps unknown state to null', () => {
    expect(mapActualToRuntime('some_unknown_state')).toBeNull();
  });
});
