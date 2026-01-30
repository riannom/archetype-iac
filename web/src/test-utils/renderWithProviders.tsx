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
  email: string;
  is_admin: boolean;
  is_active: boolean;
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
  email: "test@example.com",
  is_admin: false,
  is_active: true,
};

/**
 * Admin test user
 */
export const adminTestUser: TestUser = {
  id: "admin-user-1",
  email: "admin@example.com",
  is_admin: true,
  is_active: true,
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
