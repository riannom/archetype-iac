import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FilterChip from "./FilterChip";

describe("FilterChip", () => {
  const defaultProps = {
    label: "Test Filter",
    isActive: false,
    onClick: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("rendering", () => {
    it("renders the label", () => {
      render(<FilterChip {...defaultProps} />);
      expect(screen.getByText("Test Filter")).toBeInTheDocument();
    });

    it("renders as a button", () => {
      render(<FilterChip {...defaultProps} />);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });

    it("renders count when provided", () => {
      render(<FilterChip {...defaultProps} count={5} />);
      expect(screen.getByText("5")).toBeInTheDocument();
    });

    it("does not render count when not provided", () => {
      render(<FilterChip {...defaultProps} />);
      expect(screen.queryByText("0")).not.toBeInTheDocument();
    });

    it("renders count of 0 when explicitly provided", () => {
      render(<FilterChip {...defaultProps} count={0} />);
      expect(screen.getByText("0")).toBeInTheDocument();
    });
  });

  describe("active state styling", () => {
    it("applies active styling when isActive is true", () => {
      render(<FilterChip {...defaultProps} isActive={true} />);
      const button = screen.getByRole("button");
      expect(button).toHaveClass("bg-sage-600");
      expect(button).toHaveClass("text-white");
    });

    it("applies inactive styling when isActive is false", () => {
      render(<FilterChip {...defaultProps} isActive={false} />);
      const button = screen.getByRole("button");
      expect(button).toHaveClass("bg-stone-100");
    });

    it("applies active count styling when isActive is true", () => {
      const { container } = render(<FilterChip {...defaultProps} isActive={true} count={5} />);
      const countSpan = container.querySelector(".bg-sage-700");
      expect(countSpan).toBeInTheDocument();
    });

    it("applies inactive count styling when isActive is false", () => {
      const { container } = render(<FilterChip {...defaultProps} isActive={false} count={5} />);
      const countSpan = container.querySelector(".bg-stone-200");
      expect(countSpan).toBeInTheDocument();
    });
  });

  describe("status variant", () => {
    it("does not render status dot for default variant", () => {
      const { container } = render(
        <FilterChip {...defaultProps} variant="default" statusColor="green" />
      );
      const dots = container.querySelectorAll(".rounded-full.w-2.h-2");
      expect(dots.length).toBe(0);
    });

    it("renders green status dot for status variant", () => {
      const { container } = render(
        <FilterChip {...defaultProps} variant="status" statusColor="green" />
      );
      const dot = container.querySelector(".bg-emerald-500");
      expect(dot).toBeInTheDocument();
    });

    it("renders blue status dot for status variant", () => {
      const { container } = render(
        <FilterChip {...defaultProps} variant="status" statusColor="blue" />
      );
      const dot = container.querySelector(".bg-blue-500");
      expect(dot).toBeInTheDocument();
    });

    it("renders amber status dot for status variant", () => {
      const { container } = render(
        <FilterChip {...defaultProps} variant="status" statusColor="amber" />
      );
      const dot = container.querySelector(".bg-amber-500");
      expect(dot).toBeInTheDocument();
    });

    it("does not render status dot when statusColor is not provided", () => {
      const { container } = render(<FilterChip {...defaultProps} variant="status" />);
      const dots = container.querySelectorAll(".rounded-full.w-2.h-2");
      expect(dots.length).toBe(0);
    });
  });

  describe("click handling", () => {
    it("calls onClick when clicked", async () => {
      const user = userEvent.setup();
      const onClick = vi.fn();
      render(<FilterChip {...defaultProps} onClick={onClick} />);

      await user.click(screen.getByRole("button"));
      expect(onClick).toHaveBeenCalledTimes(1);
    });

    it("calls onClick multiple times on multiple clicks", async () => {
      const user = userEvent.setup();
      const onClick = vi.fn();
      render(<FilterChip {...defaultProps} onClick={onClick} />);

      await user.click(screen.getByRole("button"));
      await user.click(screen.getByRole("button"));
      await user.click(screen.getByRole("button"));
      expect(onClick).toHaveBeenCalledTimes(3);
    });
  });

  describe("accessibility", () => {
    it("is keyboard accessible", async () => {
      const user = userEvent.setup();
      const onClick = vi.fn();
      render(<FilterChip {...defaultProps} onClick={onClick} />);

      const button = screen.getByRole("button");
      button.focus();
      await user.keyboard("{Enter}");

      expect(onClick).toHaveBeenCalledTimes(1);
    });

    it("can be activated with space key", async () => {
      const user = userEvent.setup();
      const onClick = vi.fn();
      render(<FilterChip {...defaultProps} onClick={onClick} />);

      const button = screen.getByRole("button");
      button.focus();
      await user.keyboard(" ");

      expect(onClick).toHaveBeenCalledTimes(1);
    });
  });

  describe("styling classes", () => {
    it("has transition styling", () => {
      render(<FilterChip {...defaultProps} />);
      const button = screen.getByRole("button");
      expect(button).toHaveClass("transition-all");
    });

    it("has uppercase text styling", () => {
      render(<FilterChip {...defaultProps} />);
      const button = screen.getByRole("button");
      expect(button).toHaveClass("uppercase");
    });

    it("has font-bold styling", () => {
      render(<FilterChip {...defaultProps} />);
      const button = screen.getByRole("button");
      expect(button).toHaveClass("font-bold");
    });
  });

  describe("edge cases", () => {
    it("handles empty label", () => {
      render(<FilterChip {...defaultProps} label="" />);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });

    it("handles long label", () => {
      const longLabel = "This is a very long filter chip label that might overflow";
      render(<FilterChip {...defaultProps} label={longLabel} />);
      expect(screen.getByText(longLabel)).toBeInTheDocument();
    });

    it("handles large count numbers", () => {
      render(<FilterChip {...defaultProps} count={99999} />);
      expect(screen.getByText("99999")).toBeInTheDocument();
    });
  });
});
