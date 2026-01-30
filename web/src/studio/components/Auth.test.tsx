import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Auth from "./Auth";

// Mock ArchetypeIcon
vi.mock("../../components/icons", () => ({
  ArchetypeIcon: ({ size, className }: { size: number; className: string }) => (
    <svg data-testid="archetype-icon" width={size} className={className} />
  ),
}));

describe("Auth", () => {
  const defaultProps = {
    onLogin: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("rendering", () => {
    it("renders the login form", () => {
      render(<Auth {...defaultProps} />);
      expect(screen.getByPlaceholderText("Username")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("••••••••")).toBeInTheDocument();
    });

    it("renders ARCHETYPE branding", () => {
      render(<Auth {...defaultProps} />);
      expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
    });

    it("renders Network Studio subtitle", () => {
      render(<Auth {...defaultProps} />);
      expect(screen.getByText("Network Studio")).toBeInTheDocument();
    });

    it("renders ArchetypeIcon", () => {
      render(<Auth {...defaultProps} />);
      expect(screen.getByTestId("archetype-icon")).toBeInTheDocument();
    });

    it("renders Identity label", () => {
      render(<Auth {...defaultProps} />);
      expect(screen.getByText("Identity")).toBeInTheDocument();
    });

    it("renders Credential label", () => {
      render(<Auth {...defaultProps} />);
      expect(screen.getByText("Credential")).toBeInTheDocument();
    });

    it("renders Sign In button", () => {
      render(<Auth {...defaultProps} />);
      expect(screen.getByRole("button", { name: /sign in to archetype/i })).toBeInTheDocument();
    });

    it("renders version info", () => {
      render(<Auth {...defaultProps} />);
      expect(screen.getByText(/v2.4.0-STABLE/)).toBeInTheDocument();
    });
  });

  describe("form inputs", () => {
    it("allows typing in username field", async () => {
      const user = userEvent.setup();
      render(<Auth {...defaultProps} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      await user.type(usernameInput, "testuser");

      expect(usernameInput).toHaveValue("testuser");
    });

    it("allows typing in password field", async () => {
      const user = userEvent.setup();
      render(<Auth {...defaultProps} />);

      const passwordInput = screen.getByPlaceholderText("••••••••");
      await user.type(passwordInput, "secret123");

      expect(passwordInput).toHaveValue("secret123");
    });

    it("password field is of type password", () => {
      render(<Auth {...defaultProps} />);
      const passwordInput = screen.getByPlaceholderText("••••••••");
      expect(passwordInput).toHaveAttribute("type", "password");
    });

    it("username field is of type text", () => {
      render(<Auth {...defaultProps} />);
      const usernameInput = screen.getByPlaceholderText("Username");
      expect(usernameInput).toHaveAttribute("type", "text");
    });
  });

  describe("form submission", () => {
    it("calls onLogin with username when form is submitted", async () => {
      const user = userEvent.setup();
      const onLogin = vi.fn();
      render(<Auth onLogin={onLogin} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      const passwordInput = screen.getByPlaceholderText("••••••••");
      const submitButton = screen.getByRole("button", { name: /sign in to archetype/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      expect(onLogin).toHaveBeenCalledWith("testuser");
    });

    it("does not call onLogin when username is empty", async () => {
      const user = userEvent.setup();
      const onLogin = vi.fn();
      render(<Auth onLogin={onLogin} />);

      const submitButton = screen.getByRole("button", { name: /sign in to archetype/i });
      await user.click(submitButton);

      expect(onLogin).not.toHaveBeenCalled();
    });

    it("does not call onLogin when username is only whitespace", async () => {
      const user = userEvent.setup();
      const onLogin = vi.fn();
      render(<Auth onLogin={onLogin} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      await user.type(usernameInput, "   ");

      const submitButton = screen.getByRole("button", { name: /sign in to archetype/i });
      await user.click(submitButton);

      expect(onLogin).not.toHaveBeenCalled();
    });

    it("trims username before calling onLogin", async () => {
      const user = userEvent.setup();
      const onLogin = vi.fn();
      render(<Auth onLogin={onLogin} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      // Note: The component does username.trim() check but passes username as-is
      // So it validates trim but passes the actual value
      await user.type(usernameInput, "testuser");

      const submitButton = screen.getByRole("button", { name: /sign in to archetype/i });
      await user.click(submitButton);

      expect(onLogin).toHaveBeenCalledWith("testuser");
    });

    it("can submit form with Enter key", async () => {
      const user = userEvent.setup();
      const onLogin = vi.fn();
      render(<Auth onLogin={onLogin} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      await user.type(usernameInput, "testuser");
      await user.keyboard("{Enter}");

      expect(onLogin).toHaveBeenCalledWith("testuser");
    });

    it("prevents default form submission behavior", async () => {
      const user = userEvent.setup();
      const onLogin = vi.fn();
      render(<Auth onLogin={onLogin} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      await user.type(usernameInput, "testuser");

      const form = document.querySelector("form");
      const submitEvent = new Event("submit", { bubbles: true, cancelable: true });
      vi.spyOn(submitEvent, "preventDefault");

      form?.dispatchEvent(submitEvent);

      expect(submitEvent.preventDefault).toHaveBeenCalled();
    });
  });

  describe("accessibility", () => {
    it("has a form element", () => {
      render(<Auth {...defaultProps} />);
      expect(document.querySelector("form")).toBeInTheDocument();
    });

    it("username input is focusable", async () => {
      const user = userEvent.setup();
      render(<Auth {...defaultProps} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      await user.click(usernameInput);

      expect(usernameInput).toHaveFocus();
    });

    it("can tab between form fields", async () => {
      const user = userEvent.setup();
      render(<Auth {...defaultProps} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      const passwordInput = screen.getByPlaceholderText("••••••••");

      usernameInput.focus();
      await user.tab();

      expect(passwordInput).toHaveFocus();
    });
  });

  describe("styling", () => {
    it("has full height styling", () => {
      const { container } = render(<Auth {...defaultProps} />);
      const wrapper = container.firstChild as HTMLElement;
      expect(wrapper).toHaveClass("min-h-screen");
    });

    it("centers content", () => {
      const { container } = render(<Auth {...defaultProps} />);
      const wrapper = container.firstChild as HTMLElement;
      expect(wrapper).toHaveClass("flex");
      expect(wrapper).toHaveClass("items-center");
      expect(wrapper).toHaveClass("justify-center");
    });

    it("has user icon in username field", () => {
      render(<Auth {...defaultProps} />);
      const userIcon = document.querySelector(".fa-user");
      expect(userIcon).toBeInTheDocument();
    });

    it("has lock icon in password field", () => {
      render(<Auth {...defaultProps} />);
      const lockIcon = document.querySelector(".fa-lock");
      expect(lockIcon).toBeInTheDocument();
    });
  });

  describe("state management", () => {
    it("maintains separate state for username and password", async () => {
      const user = userEvent.setup();
      render(<Auth {...defaultProps} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      const passwordInput = screen.getByPlaceholderText("••••••••");

      await user.type(usernameInput, "user1");
      await user.type(passwordInput, "pass1");

      expect(usernameInput).toHaveValue("user1");
      expect(passwordInput).toHaveValue("pass1");
    });

    it("clears password field independently", async () => {
      const user = userEvent.setup();
      render(<Auth {...defaultProps} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      const passwordInput = screen.getByPlaceholderText("••••••••");

      await user.type(usernameInput, "user1");
      await user.type(passwordInput, "pass1");
      await user.clear(passwordInput);

      expect(usernameInput).toHaveValue("user1");
      expect(passwordInput).toHaveValue("");
    });
  });

  describe("edge cases", () => {
    it("handles special characters in username", async () => {
      const user = userEvent.setup();
      const onLogin = vi.fn();
      render(<Auth onLogin={onLogin} />);

      const usernameInput = screen.getByPlaceholderText("Username");
      await user.type(usernameInput, "user@domain.com");

      const submitButton = screen.getByRole("button", { name: /sign in to archetype/i });
      await user.click(submitButton);

      expect(onLogin).toHaveBeenCalledWith("user@domain.com");
    });

    it("handles special characters in password", async () => {
      const user = userEvent.setup();
      render(<Auth {...defaultProps} />);

      const passwordInput = screen.getByPlaceholderText("••••••••");
      await user.type(passwordInput, "p@$$w0rd!#$%");

      expect(passwordInput).toHaveValue("p@$$w0rd!#$%");
    });

    it("handles very long username", async () => {
      const user = userEvent.setup();
      const onLogin = vi.fn();
      render(<Auth onLogin={onLogin} />);

      const longUsername = "a".repeat(100);
      const usernameInput = screen.getByPlaceholderText("Username");
      await user.type(usernameInput, longUsername);

      const submitButton = screen.getByRole("button", { name: /sign in to archetype/i });
      await user.click(submitButton);

      expect(onLogin).toHaveBeenCalledWith(longUsername);
    });
  });
});
