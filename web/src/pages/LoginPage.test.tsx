import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter } from "react-router-dom";
import { LoginPage } from "./LoginPage";

// Mock fetch globally
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function renderLoginPage() {
  return render(
    <BrowserRouter>
      <LoginPage />
    </BrowserRouter>
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mockFetch.mockReset();
  });

  describe("rendering", () => {
    it("renders login form", () => {
      renderLoginPage();

      expect(screen.getByText("Sign in")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("you@lab.dev")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("••••••••")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
    });

    it("renders email input field", () => {
      renderLoginPage();

      const emailInput = screen.getByPlaceholderText("you@lab.dev");
      expect(emailInput).toBeInTheDocument();
    });

    it("renders password input field", () => {
      renderLoginPage();

      const passwordInput = screen.getByPlaceholderText("••••••••");
      expect(passwordInput).toHaveAttribute("type", "password");
    });

    it("renders register link", () => {
      renderLoginPage();

      expect(screen.getByText("Create an account")).toBeInTheDocument();
    });
  });

  describe("form validation", () => {
    it("accepts valid email", async () => {
      const user = userEvent.setup();
      renderLoginPage();

      const emailInput = screen.getByPlaceholderText("you@lab.dev");
      await user.type(emailInput, "valid@example.com");

      expect(emailInput).toHaveValue("valid@example.com");
    });

    it("accepts password input", async () => {
      const user = userEvent.setup();
      renderLoginPage();

      const passwordInput = screen.getByPlaceholderText("••••••••");
      await user.type(passwordInput, "mypassword123");

      expect(passwordInput).toHaveValue("mypassword123");
    });
  });

  describe("form submission", () => {
    it("submits form with credentials", async () => {
      const user = userEvent.setup();

      // Login success response
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: "jwt-token",
            token_type: "bearer",
          }),
      });

      renderLoginPage();

      await user.type(screen.getByPlaceholderText("you@lab.dev"), "test@example.com");
      await user.type(screen.getByPlaceholderText("••••••••"), "password123");
      await user.click(screen.getByRole("button", { name: /sign in/i }));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalled();
        expect(localStorage.getItem("token")).toBe("jwt-token");
      });
    });

    it("shows error message on failed login", async () => {
      const user = userEvent.setup();

      // Failed login response
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: () => Promise.resolve("Invalid email or password"),
      });

      renderLoginPage();

      await user.type(screen.getByPlaceholderText("you@lab.dev"), "wrong@example.com");
      await user.type(screen.getByPlaceholderText("••••••••"), "wrongpassword");
      await user.click(screen.getByRole("button", { name: /sign in/i }));

      await waitFor(() => {
        expect(screen.getByText("Invalid email or password")).toBeInTheDocument();
      });
    });

    it("navigates to labs page after successful login", async () => {
      const user = userEvent.setup();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: "jwt-token",
            token_type: "bearer",
          }),
      });

      renderLoginPage();

      await user.type(screen.getByPlaceholderText("you@lab.dev"), "test@example.com");
      await user.type(screen.getByPlaceholderText("••••••••"), "password123");
      await user.click(screen.getByRole("button", { name: /sign in/i }));

      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith("/labs");
      });
    });
  });

  describe("keyboard navigation", () => {
    it("can submit form with Enter key", async () => {
      const user = userEvent.setup();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: "token",
            token_type: "bearer",
          }),
      });

      renderLoginPage();

      await user.type(screen.getByPlaceholderText("you@lab.dev"), "test@example.com");
      await user.type(screen.getByPlaceholderText("••••••••"), "password123");
      await user.keyboard("{Enter}");

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalled();
      });
    });
  });
});
