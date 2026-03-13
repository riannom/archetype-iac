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

function hasMinGlobalRole(user: User | null, minRole: GlobalRole): boolean {
  if (!user) return false;
  const userRank = GLOBAL_ROLE_RANK[user.global_role] ?? 0;
  const minRank = GLOBAL_ROLE_RANK[minRole] ?? 0;
  return userRank >= minRank;
}

export function canManageImages(user: User | null): boolean {
  return hasMinGlobalRole(user, 'admin');
}

export function canManageUsers(user: User | null): boolean {
  return hasMinGlobalRole(user, 'super_admin');
}

export function canViewInfrastructure(user: User | null): boolean {
  return hasMinGlobalRole(user, 'admin');
}
