/**
 * Route guard component that restricts access based on global role.
 */
import { Navigate } from 'react-router-dom';
import { useUser } from '../contexts/UserContext';
import type { GlobalRole } from '../contexts/UserContext';
import { hasMinGlobalRole } from '../utils/permissions';

interface ProtectedRouteProps {
  children: React.ReactNode;
  minRole: GlobalRole;
  fallbackPath?: string;
}

export function ProtectedRoute({
  children,
  minRole,
  fallbackPath = '/',
}: ProtectedRouteProps) {
  const { user, loading } = useUser();

  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  if (!hasMinGlobalRole(user, minRole)) return <Navigate to={fallbackPath} replace />;

  return <>{children}</>;
}
