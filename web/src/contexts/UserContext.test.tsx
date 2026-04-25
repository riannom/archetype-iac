import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserProvider, useUser } from "./UserContext";

// Mock fetch globally
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

// Test component to access context
function TestConsumer() {
  const { user, loading, error, refreshUser, clearUser } = useUser();

  return (
    <div>
      <span data-testid="loading">{loading ? "loading" : "not-loading"}</span>
      <span data-testid="authenticated">
        {user ? "authenticated" : "not-authenticated"}
      </span>
      <span data-testid="user-email">{user?.email || "no-user"}</span>
      <span data-testid="error">{error || "no-error"}</span>
      <button data-testid="refresh-btn" onClick={refreshUser}>
        Refresh
      </button>
      <button data-testid="clear-btn" onClick={clearUser}>
        Clear
      </button>
    </div>
  );
}

describe("UserContext", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
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
      localStorage.setItem("token", "test-token");
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "existing@example.com",
            username: "testuser",
            global_role: "operator",
            is_active: true,
            created_at: "2024-01-01T00:00:00Z",
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

  describe("refresh user", () => {
    it("refreshes user data", async () => {
      localStorage.setItem("token", "test-token");

      // Initial load
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "test@example.com",
            username: "testuser",
            global_role: "operator",
            is_active: true,
            created_at: "2024-01-01T00:00:00Z",
          }),
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

      // Refresh
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            username: "admin",
            email: "updated@example.com",
            global_role: "super_admin",
            is_active: true,
            created_at: "2024-01-01T00:00:00Z",
          }),
      });

      await user.click(screen.getByTestId("refresh-btn"));

      await waitFor(() => {
        expect(screen.getByTestId("user-email")).toHaveTextContent(
          "updated@example.com"
        );
      });
    });
  });

  describe("non-401 fetch failure", () => {
    it("surfaces error and clears user when /auth/me responds non-ok with status != 401", async () => {
      localStorage.setItem("token", "valid-token");
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
      });

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("error")).toHaveTextContent("Failed to fetch user");
        expect(screen.getByTestId("authenticated")).toHaveTextContent("not-authenticated");
      });
      // Token must NOT be removed for non-401 errors
      expect(localStorage.getItem("token")).toBe("valid-token");
    });

    it("surfaces error message when fetch itself rejects with an Error", async () => {
      localStorage.setItem("token", "valid-token");
      mockFetch.mockRejectedValueOnce(new Error("network down"));

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("error")).toHaveTextContent("network down");
      });
    });

    it("falls back to 'Failed to fetch user' for non-Error rejections", async () => {
      localStorage.setItem("token", "valid-token");
      mockFetch.mockRejectedValueOnce("kaboom");

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("error")).toHaveTextContent("Failed to fetch user");
      });
    });
  });

  describe("storage listener", () => {
    it("refetches user when token changes in another tab", async () => {
      // Initial mount: no token
      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );
      await waitFor(() => {
        expect(screen.getByTestId("authenticated")).toHaveTextContent("not-authenticated");
      });

      // Simulate another tab logging in
      localStorage.setItem("token", "from-other-tab");
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "u",
            username: "u",
            email: "other-tab@example.com",
            global_role: "operator",
            is_active: true,
            created_at: "2024-01-01T00:00:00Z",
          }),
      });
      await act(async () => {
        window.dispatchEvent(new StorageEvent("storage", { key: "token", newValue: "from-other-tab" }));
      });

      await waitFor(() => {
        expect(screen.getByTestId("user-email")).toHaveTextContent("other-tab@example.com");
      });
    });

    it("ignores storage events for unrelated keys", async () => {
      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );
      await waitFor(() => {
        expect(screen.getByTestId("authenticated")).toHaveTextContent("not-authenticated");
      });

      mockFetch.mockClear();
      await act(async () => {
        window.dispatchEvent(new StorageEvent("storage", { key: "theme", newValue: "dark" }));
      });
      // No additional fetch should have been triggered
      expect(mockFetch).not.toHaveBeenCalled();
    });
  });

  describe("useUser outside provider", () => {
    it("throws a descriptive error", () => {
      expect(() => renderHook(() => useUser())).toThrow(
        /useUser must be used within a UserProvider/
      );
    });
  });

  describe("clear user", () => {
    it("clears user data", async () => {
      localStorage.setItem("token", "test-token");

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "test@example.com",
            username: "testuser",
            global_role: "operator",
            is_active: true,
            created_at: "2024-01-01T00:00:00Z",
          }),
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

      await user.click(screen.getByTestId("clear-btn"));

      await waitFor(() => {
        expect(screen.getByTestId("user-email")).toHaveTextContent("no-user");
      });
    });

    it("also clears any prior error state", async () => {
      localStorage.setItem("token", "valid-token");
      mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });

      const user = userEvent.setup();
      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("error")).toHaveTextContent("Failed to fetch user");
      });

      await user.click(screen.getByTestId("clear-btn"));
      await waitFor(() => {
        expect(screen.getByTestId("error")).toHaveTextContent("no-error");
      });
    });
  });

  describe("token management", () => {
    it("clears user when token is invalid", async () => {
      localStorage.setItem("token", "invalid-token");

      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      render(
        <UserProvider>
          <TestConsumer />
        </UserProvider>
      );

      await waitFor(() => {
        expect(screen.getByTestId("authenticated")).toHaveTextContent(
          "not-authenticated"
        );
        expect(localStorage.getItem("token")).toBeNull();
      });
    });

    it("loads user when valid token exists", async () => {
      localStorage.setItem("token", "valid-token");

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "user-1",
            email: "test@example.com",
            username: "testuser",
            global_role: "operator",
            is_active: true,
            created_at: "2024-01-01T00:00:00Z",
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
      });
    });
  });
});
