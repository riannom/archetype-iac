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

const StudioConsolePage = React.lazy(() => import("./pages/StudioConsolePage"));
const InfrastructurePage = React.lazy(() => import("./pages/InfrastructurePage"));
const InterfaceManagerPage = React.lazy(() => import("./pages/InterfaceManagerPage"));
const NodesPage = React.lazy(() => import("./pages/NodesPage"));
const AdminSettingsPage = React.lazy(() => import("./pages/AdminSettingsPage"));
const StudioPage = React.lazy(() => import("./studio/StudioPage"));
const UserManagementPage = React.lazy(() => import("./pages/UserManagementPage"));

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
  { path: "/admin/users", element: withSuspense(<UserManagementPage />) },
  { path: "/nodes", element: withSuspense(<NodesPage />) },
  { path: "/nodes/devices", element: withSuspense(<NodesPage />) },
  { path: "/nodes/images", element: withSuspense(<NodesPage />) },
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
