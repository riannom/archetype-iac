import React from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import { ThemeProvider } from "./theme/index";
import "./theme/backgrounds.css";
import { UserProvider } from "./contexts/UserContext";
import { NotificationProvider } from "./contexts/NotificationContext";
import { ImageLibraryProvider } from "./contexts/ImageLibraryContext";
import { DeviceCatalogProvider } from "./contexts/DeviceCatalogContext";
import { ToastContainer } from "./components/ui/ToastContainer";

const CHUNK_RELOAD_KEY = "archetype:chunk-reload-attempted";

function lazyWithChunkRetry<T extends React.ComponentType<any>>(
  importer: () => Promise<{ default: T }>
) {
  return React.lazy(async () => {
    try {
      const module = await importer();
      sessionStorage.removeItem(CHUNK_RELOAD_KEY);
      return module;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const isChunkLoadError =
        /Failed to fetch dynamically imported module|Importing a module script failed|ChunkLoadError|Loading chunk/i.test(
          message
        );
      if (isChunkLoadError) {
        const alreadyRetried = sessionStorage.getItem(CHUNK_RELOAD_KEY) === "1";
        if (!alreadyRetried) {
          sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
          window.location.reload();
          // Keep suspense fallback while the browser reloads.
          return new Promise<never>(() => {});
        }
      }
      sessionStorage.removeItem(CHUNK_RELOAD_KEY);
      throw error;
    }
  });
}

const StudioConsolePage = lazyWithChunkRetry(() => import("./pages/StudioConsolePage"));
const InfrastructurePage = lazyWithChunkRetry(() => import("./pages/InfrastructurePage"));
const InterfaceManagerPage = lazyWithChunkRetry(() => import("./pages/InterfaceManagerPage"));
const NodesPage = lazyWithChunkRetry(() => import("./pages/NodesPage"));
const AdminSettingsPage = lazyWithChunkRetry(() => import("./pages/AdminSettingsPage"));
const SupportBundlesPage = lazyWithChunkRetry(() => import("./pages/SupportBundlesPage"));
const StudioPage = lazyWithChunkRetry(() => import("./studio/StudioPage"));
const UserManagementPage = lazyWithChunkRetry(() => import("./pages/UserManagementPage"));

function RouteFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center text-stone-500 dark:text-stone-400">
      Loading...
    </div>
  );
}

function withSuspense(element: React.ReactElement) {
  return <React.Suspense fallback={<RouteFallback />}>{element}</React.Suspense>;
}

const router = createBrowserRouter([
  { path: "/", element: withSuspense(<StudioPage />) },
  { path: "/hosts", element: <Navigate to="/infrastructure" replace /> },
  { path: "/infrastructure", element: withSuspense(<InfrastructurePage />) },
  { path: "/admin/interfaces", element: withSuspense(<InterfaceManagerPage />) },
  { path: "/admin/settings", element: withSuspense(<AdminSettingsPage />) },
  { path: "/admin/support-bundles", element: withSuspense(<SupportBundlesPage />) },
  { path: "/admin/users", element: withSuspense(<UserManagementPage />) },
  { path: "/nodes", element: withSuspense(<NodesPage />) },
  { path: "/nodes/devices", element: withSuspense(<NodesPage />) },
  { path: "/nodes/images", element: withSuspense(<NodesPage />) },
  { path: "/nodes/build-jobs", element: withSuspense(<NodesPage />) },
  { path: "/nodes/sync", element: withSuspense(<NodesPage />) },
  { path: "/labs", element: <Navigate to="/" replace /> },
  { path: "/labs/:labId", element: <Navigate to="/" replace /> },
  { path: "/studio", element: <Navigate to="/" replace /> },
  { path: "/studio/console/:labId/:nodeId", element: withSuspense(<StudioConsolePage />) },
  { path: "*", element: <Navigate to="/" replace /> },
]);

const root = createRoot(document.getElementById("root")!);
root.render(
  <React.StrictMode>
    <ThemeProvider>
      <UserProvider>
        <ImageLibraryProvider>
          <DeviceCatalogProvider>
            <NotificationProvider>
              <RouterProvider router={router} />
              <ToastContainer />
            </NotificationProvider>
          </DeviceCatalogProvider>
        </ImageLibraryProvider>
      </UserProvider>
    </ThemeProvider>
  </React.StrictMode>
);
