import { describe, it, expect } from 'vitest';
import { mapActualToRuntime, type NodeStateData } from './nodeState';

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

  it('maps "error" with willRetry=true but desired=stopped to "error"', () => {
    expect(mapActualToRuntime('error', 'stopped', true)).toBe('error');
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

  // Server-computed display_state tests (Phase 6.3)
  it('prefers server display_state "running"', () => {
    expect(mapActualToRuntime('running', 'running', false, 'running')).toBe('running');
  });

  it('prefers server display_state "starting" -> booting', () => {
    expect(mapActualToRuntime('pending', 'running', false, 'starting')).toBe('booting');
  });

  it('prefers server display_state "stopping"', () => {
    expect(mapActualToRuntime('stopping', 'stopped', false, 'stopping')).toBe('stopping');
  });

  it('prefers server display_state "stopped"', () => {
    expect(mapActualToRuntime('exited', 'stopped', false, 'stopped')).toBe('stopped');
  });

  it('prefers server display_state "error"', () => {
    expect(mapActualToRuntime('error', 'running', false, 'error')).toBe('error');
  });

  it('server display_state "error" with willRetry shows booting', () => {
    expect(mapActualToRuntime('error', 'running', true, 'error')).toBe('booting');
  });

  it('server display_state "error" with willRetry and desired=stopped stays error', () => {
    expect(mapActualToRuntime('error', 'stopped', true, 'error')).toBe('error');
  });

  it('falls back to client-side when displayState is undefined', () => {
    expect(mapActualToRuntime('starting', 'running', false, undefined)).toBe('booting');
  });
});

describe('NodeStateData retry fields', () => {
  it('includes enforcement_attempts and max_enforcement_attempts', () => {
    const data: NodeStateData = {
      node_id: 'n1',
      node_name: 'R1',
      desired_state: 'running',
      actual_state: 'error',
      is_ready: false,
      will_retry: true,
      enforcement_attempts: 2,
      max_enforcement_attempts: 5,
      display_state: 'error',
    };
    expect(data.enforcement_attempts).toBe(2);
    expect(data.max_enforcement_attempts).toBe(5);
    expect(data.will_retry).toBe(true);
  });

  it('defaults enforcement fields to undefined when not provided', () => {
    const data: NodeStateData = {
      node_id: 'n1',
      node_name: 'R1',
      desired_state: 'running',
      actual_state: 'running',
      is_ready: true,
    };
    expect(data.enforcement_attempts).toBeUndefined();
    expect(data.max_enforcement_attempts).toBeUndefined();
  });
});
