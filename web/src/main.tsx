import React from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import StudioConsolePage from "./pages/StudioConsolePage";
import StudioPage from "./studio/StudioPage";

const router = createBrowserRouter([
  { path: "/", element: <StudioPage /> },
  { path: "/labs", element: <Navigate to="/" replace /> },
  { path: "/labs/:labId", element: <Navigate to="/" replace /> },
  { path: "/studio", element: <Navigate to="/" replace /> },
  { path: "/studio/console/:labId/:nodeId", element: <StudioConsolePage /> },
  { path: "*", element: <Navigate to="/" replace /> },
]);

const root = createRoot(document.getElementById("root")!);
root.render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
