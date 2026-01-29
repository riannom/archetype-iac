import React from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import { ThemeProvider } from "./theme/index";
import { UserProvider } from "./contexts/UserContext";
import StudioConsolePage from "./pages/StudioConsolePage";
import HostsPage from "./pages/HostsPage";
import NodesPage from "./pages/NodesPage";
import StudioPage from "./studio/StudioPage";

const router = createBrowserRouter([
  { path: "/", element: <StudioPage /> },
  { path: "/hosts", element: <HostsPage /> },
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
        <RouterProvider router={router} />
      </UserProvider>
    </ThemeProvider>
  </React.StrictMode>
);
