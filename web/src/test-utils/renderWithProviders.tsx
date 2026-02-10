import React, { ReactElement } from "react";
import { render, RenderOptions } from "@testing-library/react";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "../theme/ThemeProvider";
import { UserProvider } from "../contexts/UserContext";

/**
 * User for testing purposes
 */
export interface TestUser {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  global_role: string;
  created_at: string;
}

/**
 * Options for renderWithProviders
 */
interface CustomRenderOptions extends Omit<RenderOptions, "wrapper"> {
  /**
   * Initial route path for MemoryRouter
   */
  initialPath?: string;
  /**
   * Use MemoryRouter instead of BrowserRouter
   */
  useMemoryRouter?: boolean;
  /**
   * Mock user to provide to UserContext
   */
  user?: TestUser | null;
  /**
   * Whether user is loading
   */
  userLoading?: boolean;
}

/**
 * Default test user
 */
export const defaultTestUser: TestUser = {
  id: "test-user-1",
  username: "testuser",
  email: "test@example.com",
  is_active: true,
  global_role: "operator",
  created_at: "2024-01-01T00:00:00Z",
};

/**
 * Admin test user
 */
export const adminTestUser: TestUser = {
  id: "admin-user-1",
  username: "admin",
  email: "admin@example.com",
  is_active: true,
  global_role: "super_admin",
  created_at: "2024-01-01T00:00:00Z",
};

/**
 * Creates a wrapper component with all providers
 */
function createWrapper({
  initialPath = "/",
  useMemoryRouter = false,
}: CustomRenderOptions = {}) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    const RouterComponent = useMemoryRouter ? MemoryRouter : BrowserRouter;
    const routerProps = useMemoryRouter ? { initialEntries: [initialPath] } : {};

    return (
      <RouterComponent {...routerProps}>
        <ThemeProvider>{children}</ThemeProvider>
      </RouterComponent>
    );
  };
}

/**
 * Render with all providers (Router, Theme)
 *
 * @example
 * ```tsx
 * const { getByText } = renderWithProviders(<MyComponent />);
 * ```
 */
export function renderWithProviders(
  ui: ReactElement,
  options: CustomRenderOptions = {}
) {
  const { initialPath, useMemoryRouter, ...renderOptions } = options;

  return render(ui, {
    wrapper: createWrapper({ initialPath, useMemoryRouter }),
    ...renderOptions,
  });
}

/**
 * Render with MemoryRouter for testing route-specific behavior
 *
 * @example
 * ```tsx
 * const { getByText } = renderWithMemoryRouter(<MyPage />, { initialPath: '/labs/123' });
 * ```
 */
export function renderWithMemoryRouter(
  ui: ReactElement,
  options: Omit<CustomRenderOptions, "useMemoryRouter"> = {}
) {
  return renderWithProviders(ui, { ...options, useMemoryRouter: true });
}

/**
 * Re-export everything from testing-library
 */
export * from "@testing-library/react";
export { default as userEvent } from "@testing-library/user-event";
