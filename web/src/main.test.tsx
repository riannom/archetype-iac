import React from 'react';
import { vi } from 'vitest';

const { renderSpy, createRootSpy, createBrowserRouterSpy } = vi.hoisted(() => {
  const renderSpy = vi.fn();
  const createRootSpy = vi.fn(() => ({ render: renderSpy }));
  const createBrowserRouterSpy = vi.fn(() => ({ routes: 'router' }));
  document.body.innerHTML = '<div id="root"></div>';
  return { renderSpy, createRootSpy, createBrowserRouterSpy };
});

vi.mock('react-dom/client', () => ({
  createRoot: createRootSpy,
}));

vi.mock('react-router-dom', () => ({
  createBrowserRouter: createBrowserRouterSpy,
  RouterProvider: ({ router }: { router: unknown }) => (
    <div data-testid="router-provider" data-router={String(Boolean(router))} />
  ),
  Navigate: () => <div data-testid="navigate" />,
}));

vi.mock('./theme/index', () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="theme">{children}</div>
  ),
}));

vi.mock('./contexts/UserContext', () => ({
  UserProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="user">{children}</div>
  ),
}));

vi.mock('./contexts/NotificationContext', () => ({
  NotificationProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="notifications">{children}</div>
  ),
}));

vi.mock('./contexts/ImageLibraryContext', () => ({
  ImageLibraryProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="images">{children}</div>
  ),
}));

vi.mock('./contexts/DeviceCatalogContext', () => ({
  DeviceCatalogProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="devices">{children}</div>
  ),
}));

vi.mock('./components/ui/ToastContainer', () => ({
  ToastContainer: () => <div data-testid="toast-container" />,
}));

vi.mock('./pages/StudioConsolePage', () => ({
  default: () => <div data-testid="studio-console" />,
}));

vi.mock('./pages/InfrastructurePage', () => ({
  default: () => <div data-testid="infrastructure" />,
}));

vi.mock('./pages/NodesPage', () => ({
  default: () => <div data-testid="nodes" />,
}));

vi.mock('./pages/AdminSettingsPage', () => ({
  default: () => <div data-testid="admin-settings" />,
}));

vi.mock('./studio/StudioPage', () => ({
  default: () => <div data-testid="studio" />,
}));

vi.mock('./theme/backgrounds.css', () => ({}));

import * as mainEntry from './main';

describe('main entry', () => {
  it('renders the app into #root', () => {
    void mainEntry;
    expect(createRootSpy).toHaveBeenCalled();
    expect(renderSpy).toHaveBeenCalled();
    expect(createBrowserRouterSpy).toHaveBeenCalled();
  });
});
