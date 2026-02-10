import { describe, it, expect } from 'vitest';
import {
  hasMinGlobalRole,
  canCreateLab,
  canManageImages,
  canManageAgents,
  canManageUsers,
  canViewInfrastructure,
  hasMinLabRole,
  canEditLab,
  canDeleteLab,
  type LabRole,
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

describe('hasMinGlobalRole', () => {
  it('returns false for null user', () => {
    expect(hasMinGlobalRole(null, 'viewer')).toBe(false);
  });

  it('returns true when user role matches min role', () => {
    expect(hasMinGlobalRole(createUser('operator'), 'operator')).toBe(true);
  });

  it('returns true when user role exceeds min role', () => {
    expect(hasMinGlobalRole(createUser('admin'), 'viewer')).toBe(true);
    expect(hasMinGlobalRole(createUser('super_admin'), 'operator')).toBe(true);
  });

  it('returns false when user role is below min role', () => {
    expect(hasMinGlobalRole(createUser('viewer'), 'operator')).toBe(false);
    expect(hasMinGlobalRole(createUser('operator'), 'admin')).toBe(false);
  });

  it('follows correct rank hierarchy: super_admin > admin > operator > viewer', () => {
    const roles: GlobalRole[] = ['viewer', 'operator', 'admin', 'super_admin'];
    roles.forEach((userRole, i) => {
      roles.forEach((minRole, j) => {
        expect(hasMinGlobalRole(createUser(userRole), minRole)).toBe(i >= j);
      });
    });
  });
});

describe('canCreateLab', () => {
  it('returns false for null user', () => {
    expect(canCreateLab(null)).toBe(false);
  });

  it('returns false for viewer', () => {
    expect(canCreateLab(createUser('viewer'))).toBe(false);
  });

  it('returns true for operator', () => {
    expect(canCreateLab(createUser('operator'))).toBe(true);
  });

  it('returns true for admin', () => {
    expect(canCreateLab(createUser('admin'))).toBe(true);
  });

  it('returns true for super_admin', () => {
    expect(canCreateLab(createUser('super_admin'))).toBe(true);
  });
});

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

describe('canManageAgents', () => {
  it('returns false for null user', () => {
    expect(canManageAgents(null)).toBe(false);
  });

  it('returns false for viewer', () => {
    expect(canManageAgents(createUser('viewer'))).toBe(false);
  });

  it('returns false for operator', () => {
    expect(canManageAgents(createUser('operator'))).toBe(false);
  });

  it('returns true for admin', () => {
    expect(canManageAgents(createUser('admin'))).toBe(true);
  });

  it('returns true for super_admin', () => {
    expect(canManageAgents(createUser('super_admin'))).toBe(true);
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

describe('hasMinLabRole', () => {
  it('returns false for null role', () => {
    expect(hasMinLabRole(null, 'viewer')).toBe(false);
  });

  it('returns false for undefined role', () => {
    expect(hasMinLabRole(undefined, 'viewer')).toBe(false);
  });

  it('returns true when role matches min role', () => {
    expect(hasMinLabRole('editor', 'editor')).toBe(true);
  });

  it('returns true when role exceeds min role', () => {
    expect(hasMinLabRole('owner', 'viewer')).toBe(true);
    expect(hasMinLabRole('editor', 'viewer')).toBe(true);
    expect(hasMinLabRole('owner', 'editor')).toBe(true);
  });

  it('returns false when role is below min role', () => {
    expect(hasMinLabRole('viewer', 'editor')).toBe(false);
    expect(hasMinLabRole('editor', 'owner')).toBe(false);
  });

  it('follows correct rank hierarchy: owner > editor > viewer', () => {
    const roles: LabRole[] = ['viewer', 'editor', 'owner'];
    roles.forEach((userRole, i) => {
      roles.forEach((minRole, j) => {
        expect(hasMinLabRole(userRole, minRole)).toBe(i >= j);
      });
    });
  });
});

describe('canEditLab', () => {
  it('returns false for null role', () => {
    expect(canEditLab(null)).toBe(false);
  });

  it('returns false for undefined role', () => {
    expect(canEditLab(undefined)).toBe(false);
  });

  it('returns false for viewer', () => {
    expect(canEditLab('viewer')).toBe(false);
  });

  it('returns true for editor', () => {
    expect(canEditLab('editor')).toBe(true);
  });

  it('returns true for owner', () => {
    expect(canEditLab('owner')).toBe(true);
  });
});

describe('canDeleteLab', () => {
  it('returns false for null role', () => {
    expect(canDeleteLab(null)).toBe(false);
  });

  it('returns false for undefined role', () => {
    expect(canDeleteLab(undefined)).toBe(false);
  });

  it('returns false for viewer', () => {
    expect(canDeleteLab('viewer')).toBe(false);
  });

  it('returns false for editor', () => {
    expect(canDeleteLab('editor')).toBe(false);
  });

  it('returns true for owner', () => {
    expect(canDeleteLab('owner')).toBe(true);
  });
});
