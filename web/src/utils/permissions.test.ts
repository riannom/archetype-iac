import { describe, it, expect } from 'vitest';
import {
  canManageImages,
  canManageUsers,
  canViewInfrastructure,
} from './permissions';
import type { User, GlobalRole } from '../contexts/UserContext';

function createUser(global_role: GlobalRole): User {
  return {
    id: '1',
    username: 'testuser',
    email: 'test@example.com',
    is_active: true,
    global_role,
    created_at: '2025-01-01T00:00:00Z',
  };
}

describe('canManageImages', () => {
  it('returns false for null user', () => {
    expect(canManageImages(null)).toBe(false);
  });

  it('returns false for viewer', () => {
    expect(canManageImages(createUser('viewer'))).toBe(false);
  });

  it('returns false for operator', () => {
    expect(canManageImages(createUser('operator'))).toBe(false);
  });

  it('returns true for admin', () => {
    expect(canManageImages(createUser('admin'))).toBe(true);
  });

  it('returns true for super_admin', () => {
    expect(canManageImages(createUser('super_admin'))).toBe(true);
  });
});

describe('canManageUsers', () => {
  it('returns false for null user', () => {
    expect(canManageUsers(null)).toBe(false);
  });

  it('returns false for viewer', () => {
    expect(canManageUsers(createUser('viewer'))).toBe(false);
  });

  it('returns false for operator', () => {
    expect(canManageUsers(createUser('operator'))).toBe(false);
  });

  it('returns false for admin', () => {
    expect(canManageUsers(createUser('admin'))).toBe(false);
  });

  it('returns true for super_admin', () => {
    expect(canManageUsers(createUser('super_admin'))).toBe(true);
  });
});

describe('canViewInfrastructure', () => {
  it('returns false for null user', () => {
    expect(canViewInfrastructure(null)).toBe(false);
  });

  it('returns false for viewer', () => {
    expect(canViewInfrastructure(createUser('viewer'))).toBe(false);
  });

  it('returns false for operator', () => {
    expect(canViewInfrastructure(createUser('operator'))).toBe(false);
  });

  it('returns true for admin', () => {
    expect(canViewInfrastructure(createUser('admin'))).toBe(true);
  });

  it('returns true for super_admin', () => {
    expect(canViewInfrastructure(createUser('super_admin'))).toBe(true);
  });
});
