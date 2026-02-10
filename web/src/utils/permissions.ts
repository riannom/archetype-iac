/**
 * Role-based permission helpers for frontend UI gating.
 */
import type { User, GlobalRole } from '../contexts/UserContext';

const GLOBAL_ROLE_RANK: Record<GlobalRole, number> = {
  super_admin: 4,
  admin: 3,
  operator: 2,
  viewer: 1,
};

export function hasMinGlobalRole(user: User | null, minRole: GlobalRole): boolean {
  if (!user) return false;
  const userRank = GLOBAL_ROLE_RANK[user.global_role] ?? 0;
  const minRank = GLOBAL_ROLE_RANK[minRole] ?? 0;
  return userRank >= minRank;
}

export function canCreateLab(user: User | null): boolean {
  return hasMinGlobalRole(user, 'operator');
}

export function canManageImages(user: User | null): boolean {
  return hasMinGlobalRole(user, 'admin');
}

export function canManageAgents(user: User | null): boolean {
  return hasMinGlobalRole(user, 'admin');
}

export function canManageUsers(user: User | null): boolean {
  return hasMinGlobalRole(user, 'super_admin');
}

export function canViewInfrastructure(user: User | null): boolean {
  return hasMinGlobalRole(user, 'admin');
}

export type LabRole = 'owner' | 'editor' | 'viewer';

const LAB_ROLE_RANK: Record<LabRole, number> = {
  owner: 3,
  editor: 2,
  viewer: 1,
};

export function hasMinLabRole(userRole: LabRole | null | undefined, minRole: LabRole): boolean {
  if (!userRole) return false;
  return (LAB_ROLE_RANK[userRole] ?? 0) >= (LAB_ROLE_RANK[minRole] ?? 0);
}

export function canEditLab(userRole: LabRole | null | undefined): boolean {
  return hasMinLabRole(userRole, 'editor');
}

export function canDeleteLab(userRole: LabRole | null | undefined): boolean {
  return hasMinLabRole(userRole, 'owner');
}
