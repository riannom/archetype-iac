import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "./Button";

describe("Button", () => {
  it("renders with children text", () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole("button")).toHaveTextContent("Click me");
  });

  it("calls onClick when clicked", async () => {
    const handleClick = vi.fn();
    const user = userEvent.setup();

    render(<Button onClick={handleClick}>Click me</Button>);

    await user.click(screen.getByRole("button"));
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("does not call onClick when disabled", async () => {
    const handleClick = vi.fn();
    const user = userEvent.setup();

    render(
      <Button onClick={handleClick} disabled>
        Click me
      </Button>
    );

    await user.click(screen.getByRole("button"));
    expect(handleClick).not.toHaveBeenCalled();
  });

  describe("variants", () => {
    it("renders primary variant", () => {
      render(<Button variant="primary">Primary</Button>);
      const button = screen.getByRole("button");
      expect(button).toBeInTheDocument();
    });

    it("renders secondary variant by default", () => {
      render(<Button>Secondary</Button>);
      const button = screen.getByRole("button");
      expect(button).toBeInTheDocument();
    });

    it("renders ghost variant", () => {
      render(<Button variant="ghost">Ghost</Button>);
      const button = screen.getByRole("button");
      expect(button).toBeInTheDocument();
    });

    it("renders danger variant", () => {
      render(<Button variant="danger">Danger</Button>);
      const button = screen.getByRole("button");
      expect(button).toBeInTheDocument();
    });
  });

  describe("sizes", () => {
    it("renders small size", () => {
      render(<Button size="sm">Small</Button>);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });

    it("renders medium size by default", () => {
      render(<Button>Medium</Button>);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });

    it("renders large size", () => {
      render(<Button size="lg">Large</Button>);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });

    it("renders icon size", () => {
      render(<Button size="icon">I</Button>);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });
  });

  describe("states", () => {
    it("shows loading state", () => {
      render(<Button loading>Loading</Button>);
      const button = screen.getByRole("button");
      expect(button).toBeDisabled();
      // Loading spinner should be visible (fa-spinner icon)
      expect(button.querySelector("i.fa-spinner")).toBeInTheDocument();
    });

    it("applies disabled attribute", () => {
      render(<Button disabled>Disabled</Button>);
      expect(screen.getByRole("button")).toBeDisabled();
    });
  });

  describe("icons", () => {
    it("renders with left icon", () => {
      render(<Button leftIcon="fa-solid fa-search">Search</Button>);
      const button = screen.getByRole("button");
      expect(button.querySelector("i.fa-search")).toBeInTheDocument();
    });

    it("renders with right icon", () => {
      render(<Button rightIcon="fa-solid fa-arrow-right">Next</Button>);
      const button = screen.getByRole("button");
      expect(button.querySelector("i.fa-arrow-right")).toBeInTheDocument();
    });
  });

  describe("form integration", () => {
    it("submits form when type is submit", async () => {
      const handleSubmit = vi.fn((e) => e.preventDefault());
      const user = userEvent.setup();

      render(
        <form onSubmit={handleSubmit}>
          <Button type="submit">Submit</Button>
        </form>
      );

      await user.click(screen.getByRole("button"));
      expect(handleSubmit).toHaveBeenCalledTimes(1);
    });

    it("does not submit when type is button", async () => {
      const handleSubmit = vi.fn((e) => e.preventDefault());
      const user = userEvent.setup();

      render(
        <form onSubmit={handleSubmit}>
          <Button type="button">Button</Button>
        </form>
      );

      await user.click(screen.getByRole("button"));
      expect(handleSubmit).not.toHaveBeenCalled();
    });
  });

  describe("keyboard accessibility", () => {
    it("can be focused", () => {
      render(<Button>Focusable</Button>);
      const button = screen.getByRole("button");
      button.focus();
      expect(button).toHaveFocus();
    });

    it("activates on Enter key", async () => {
      const handleClick = vi.fn();
      const user = userEvent.setup();

      render(<Button onClick={handleClick}>Press Enter</Button>);
      const button = screen.getByRole("button");
      button.focus();
      await user.keyboard("{Enter}");

      expect(handleClick).toHaveBeenCalledTimes(1);
    });

    it("activates on Space key", async () => {
      const handleClick = vi.fn();
      const user = userEvent.setup();

      render(<Button onClick={handleClick}>Press Space</Button>);
      const button = screen.getByRole("button");
      button.focus();
      await user.keyboard(" ");

      expect(handleClick).toHaveBeenCalledTimes(1);
    });
  });
});
