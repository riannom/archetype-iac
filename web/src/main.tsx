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
import StudioConsolePage from "./pages/StudioConsolePage";
import InfrastructurePage from "./pages/InfrastructurePage";
import NodesPage from "./pages/NodesPage";
import StudioPage from "./studio/StudioPage";

const router = createBrowserRouter([
  { path: "/", element: <StudioPage /> },
  { path: "/hosts", element: <Navigate to="/infrastructure" replace /> },
  { path: "/infrastructure", element: <InfrastructurePage /> },
  { path: "/nodes", element: <NodesPage /> },
  { path: "/nodes/devices", element: <NodesPage /> },
  { path: "/nodes/images", element: <NodesPage /> },
  { path: "/nodes/sync", element: <NodesPage /> },
  { path: "/labs", element: <Navigate to="/" replace /> },
  { path: "/labs/:labId", element: <Navigate to="/" replace /> },
  { path: "/studio", element: <Navigate to="/" replace /> },
  { path: "/studio/console/:labId/:nodeId", element: <StudioConsolePage /> },
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
