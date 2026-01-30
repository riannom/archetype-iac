import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserProvider, useUser } from "./UserContext";

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

// Test component to access context
function TestConsumer() {
  const { user, isLoading, isAuthenticated, login, logout, error } = useUser();

  return (
    <div>
      <span data-testid="loading">{isLoading ? "loading" : "not-loading"}</span>
      <span data-testid="authenticated">
        {isAuthenticated ? "authenticated" : "not-authenticated"}
      </span>
      <span data-testid="user-email">{user?.email || "no-user"}</span>
      <span data-testid="error">{error || "no-error"}</span>
      <button
        data-testid="login-btn"
        onClick={() => login("test@example.com", "password123")}
      >
        Login
      </button>
      <button data-testid="logout-btn" onClick={logout}>
        Logout
      </button>
    </div>
  );
}

describe("UserContext", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // Reset fetch mock
    mockFetch.mockReset();
  });

  describe("initial state", () => {
    it("starts with loading state when checking auth", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      // Initial loading state
      expect(screen.getByTestId("loading")).toHaveTextContent("loading");

      await waitFor(() => {
        expect(screen.getByTestId("loading")).toHaveTextContent("not-loading");
      });
    });

    it("checks for existing session on mount", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "existing@example.com",
            is_admin: false,
          }),
      });

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("authenticated")).toHaveTextContent(
          "authenticated"
        );
        expect(screen.getByTestId("user-email")).toHaveTextContent(
          "existing@example.com"
        );
      });
    });
  });

  describe("login", () => {
    it("logs in successfully", async () => {
      // Initial auth check
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      // Login request
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: "jwt-token",
            token_type: "bearer",
          }),
      });

      // Get user info after login
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "test@example.com",
            is_admin: false,
          }),
      });

      const user = userEvent.setup();

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      // Wait for initial auth check
      await waitFor(() => {
        expect(screen.getByTestId("loading")).toHaveTextContent("not-loading");
      });

      // Click login
      await user.click(screen.getByTestId("login-btn"));

      await waitFor(() => {
        expect(screen.getByTestId("authenticated")).toHaveTextContent(
          "authenticated"
        );
      });
    });

    it("handles login failure", async () => {
      // Initial auth check
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      // Failed login
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: () =>
          Promise.resolve({
            detail: "Invalid credentials",
          }),
      });

      const user = userEvent.setup();

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("loading")).toHaveTextContent("not-loading");
      });

      await user.click(screen.getByTestId("login-btn"));

      await waitFor(() => {
        expect(screen.getByTestId("error")).toHaveTextContent("Invalid credentials");
        expect(screen.getByTestId("authenticated")).toHaveTextContent(
          "not-authenticated"
        );
      });
    });
  });

  describe("logout", () => {
    it("logs out successfully", async () => {
      // Initial auth check - already logged in
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "test@example.com",
            is_admin: false,
          }),
      });

      // Logout request
      mockFetch.mockResolvedValueOnce({
        ok: true,
      });

      const user = userEvent.setup();

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("authenticated")).toHaveTextContent(
          "authenticated"
        );
      });

      await user.click(screen.getByTestId("logout-btn"));

      await waitFor(() => {
        expect(screen.getByTestId("authenticated")).toHaveTextContent(
          "not-authenticated"
        );
        expect(screen.getByTestId("user-email")).toHaveTextContent("no-user");
      });
    });

    it("clears user state on logout", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "test@example.com",
            is_admin: true,
          }),
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
      });

      const user = userEvent.setup();

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("user-email")).toHaveTextContent(
          "test@example.com"
        );
      });

      await user.click(screen.getByTestId("logout-btn"));

      await waitFor(() => {
        expect(screen.getByTestId("user-email")).toHaveTextContent("no-user");
      });
    });
  });

  describe("token management", () => {
    it("stores token in localStorage after login", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: "test-jwt-token",
            token_type: "bearer",
          }),
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "test@example.com",
          }),
      });

      const user = userEvent.setup();

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("loading")).toHaveTextContent("not-loading");
      });

      await user.click(screen.getByTestId("login-btn"));

      await waitFor(() => {
        expect(localStorage.getItem("token")).toBe("test-jwt-token");
      });
    });

    it("clears token from localStorage on logout", async () => {
      localStorage.setItem("token", "existing-token");

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "test@example.com",
          }),
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
      });

      const user = userEvent.setup();

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("authenticated")).toHaveTextContent(
          "authenticated"
        );
      });

      await user.click(screen.getByTestId("logout-btn"));

      await waitFor(() => {
        expect(localStorage.getItem("token")).toBeNull();
      });
    });
  });
});
