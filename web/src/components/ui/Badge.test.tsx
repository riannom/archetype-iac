import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge, BadgeVariant, BadgeSize } from "./Badge";

describe("Badge", () => {
  describe("rendering", () => {
    it("renders children text", () => {
      render(<Badge>Test Badge</Badge>);
      expect(screen.getByText("Test Badge")).toBeInTheDocument();
    });

    it("renders as span element", () => {
      render(<Badge>Badge</Badge>);
      const badge = screen.getByText("Badge");
      expect(badge.tagName).toBe("SPAN");
    });
  });

  describe("variants", () => {
    const variants: BadgeVariant[] = [
      "default",
      "success",
      "warning",
      "error",
      "info",
      "accent",
    ];

    variants.forEach((variant) => {
      it(`renders ${variant} variant`, () => {
        render(<Badge variant={variant}>{variant}</Badge>);
        expect(screen.getByText(variant)).toBeInTheDocument();
      });
    });

    it("uses default variant when not specified", () => {
      render(<Badge>Default</Badge>);
      expect(screen.getByText("Default")).toBeInTheDocument();
    });
  });

  describe("sizes", () => {
    const sizes: BadgeSize[] = ["sm", "md"];

    sizes.forEach((size) => {
      it(`renders ${size} size`, () => {
        render(<Badge size={size}>{size}</Badge>);
        expect(screen.getByText(size)).toBeInTheDocument();
      });
    });

    it("uses md size when not specified", () => {
      render(<Badge>Medium</Badge>);
      expect(screen.getByText("Medium")).toBeInTheDocument();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<Badge className="custom-class">Styled</Badge>);
      const badge = screen.getByText("Styled");
      expect(badge).toHaveClass("custom-class");
    });

    it("has rounded-full class", () => {
      render(<Badge>Round</Badge>);
      const badge = screen.getByText("Round");
      expect(badge).toHaveClass("rounded-full");
    });

    it("has uppercase class", () => {
      render(<Badge>Uppercase</Badge>);
      const badge = screen.getByText("Uppercase");
      expect(badge).toHaveClass("uppercase");
    });

    it("has inline-flex class", () => {
      render(<Badge>Flex</Badge>);
      const badge = screen.getByText("Flex");
      expect(badge).toHaveClass("inline-flex");
    });
  });

  describe("accessibility", () => {
    it("renders accessible text content", () => {
      render(<Badge>Status: Active</Badge>);
      expect(screen.getByText("Status: Active")).toBeInTheDocument();
    });
  });
});
