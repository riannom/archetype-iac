import React from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./styles.css";
import { AuthLayout } from "./components/AuthLayout";
import { Layout } from "./components/Layout";
import { CatalogPage } from "./pages/CatalogPage";
import { LabDetailPage } from "./pages/LabDetailPage";
import { LabsPage } from "./pages/LabsPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import StudioConsolePage from "./pages/StudioConsolePage";
import StudioPage from "./studio/StudioPage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { path: "labs", element: <LabsPage /> },
      { path: "labs/:labId", element: <LabDetailPage /> },
      { path: "catalog", element: <CatalogPage /> },
      { path: "studio", element: <StudioPage /> },
      { path: "*", element: <LabsPage /> },
    ],
  },
  {
    path: "/studio/console/:labId/:nodeId",
    element: <StudioConsolePage />,
  },
  {
    path: "/auth",
    element: <AuthLayout />,
    children: [
      { path: "login", element: <LoginPage /> },
      { path: "register", element: <RegisterPage /> },
    ],
  },
]);

const root = createRoot(document.getElementById("root")!);
root.render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
