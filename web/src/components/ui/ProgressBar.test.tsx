import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProgressBar, ProgressBarVariant, ProgressBarSize } from "./ProgressBar";

describe("ProgressBar", () => {
  describe("rendering", () => {
    it("renders progress bar", () => {
      render(<ProgressBar value={50} />);
      // Check the container exists
      const container = document.querySelector(".bg-stone-200");
      expect(container).toBeInTheDocument();
    });

    it("renders with correct width style", () => {
      render(<ProgressBar value={75} />);
      const progressFill = document.querySelector("[style]");
      expect(progressFill).toHaveStyle({ width: "75%" });
    });
  });

  describe("value handling", () => {
    it("clamps value at 0", () => {
      render(<ProgressBar value={-10} />);
      const progressFill = document.querySelector("[style]");
      expect(progressFill).toHaveStyle({ width: "0%" });
    });

    it("clamps value at 100", () => {
      render(<ProgressBar value={150} />);
      const progressFill = document.querySelector("[style]");
      expect(progressFill).toHaveStyle({ width: "100%" });
    });

    it("handles zero value", () => {
      render(<ProgressBar value={0} />);
      const progressFill = document.querySelector("[style]");
      expect(progressFill).toHaveStyle({ width: "0%" });
    });

    it("handles 100 value", () => {
      render(<ProgressBar value={100} />);
      const progressFill = document.querySelector("[style]");
      expect(progressFill).toHaveStyle({ width: "100%" });
    });
  });

  describe("variants", () => {
    const variants: ProgressBarVariant[] = ["default", "cpu", "memory", "storage"];

    variants.forEach((variant) => {
      it(`renders ${variant} variant`, () => {
        render(<ProgressBar value={50} variant={variant} />);
        const container = document.querySelector(".bg-stone-200");
        expect(container).toBeInTheDocument();
      });
    });

    it("uses default variant when not specified", () => {
      render(<ProgressBar value={50} />);
      const container = document.querySelector(".bg-stone-200");
      expect(container).toBeInTheDocument();
    });
  });

  describe("sizes", () => {
    const sizes: ProgressBarSize[] = ["sm", "md", "lg"];

    sizes.forEach((size) => {
      it(`renders ${size} size`, () => {
        render(<ProgressBar value={50} size={size} />);
        const container = document.querySelector(".bg-stone-200");
        expect(container).toBeInTheDocument();
      });
    });

    it("sm size has h-1 class", () => {
      render(<ProgressBar value={50} size="sm" />);
      const container = document.querySelector(".h-1");
      expect(container).toBeInTheDocument();
    });

    it("md size has h-1.5 class", () => {
      render(<ProgressBar value={50} size="md" />);
      const container = document.querySelector(".h-1\\.5");
      expect(container).toBeInTheDocument();
    });

    it("lg size has h-2 class", () => {
      render(<ProgressBar value={50} size="lg" />);
      const container = document.querySelector(".h-2");
      expect(container).toBeInTheDocument();
    });
  });

  describe("label", () => {
    it("does not show label by default", () => {
      render(<ProgressBar value={50} />);
      expect(screen.queryByText("50%")).not.toBeInTheDocument();
    });

    it("shows label when showLabel is true", () => {
      render(<ProgressBar value={50} showLabel />);
      expect(screen.getByText("50%")).toBeInTheDocument();
    });

    it("rounds label value", () => {
      render(<ProgressBar value={33.7} showLabel />);
      expect(screen.getByText("34%")).toBeInTheDocument();
    });
  });

  describe("thresholds and colors", () => {
    it("shows normal color for low values", () => {
      render(<ProgressBar value={30} variant="cpu" />);
      const progressFill = document.querySelector(".bg-sage-500");
      expect(progressFill).toBeInTheDocument();
    });

    it("shows warning color for medium values", () => {
      render(<ProgressBar value={75} variant="cpu" />);
      const progressFill = document.querySelector(".bg-amber-500");
      expect(progressFill).toBeInTheDocument();
    });

    it("shows danger color for high values", () => {
      render(<ProgressBar value={95} variant="cpu" />);
      const progressFill = document.querySelector(".bg-red-500");
      expect(progressFill).toBeInTheDocument();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<ProgressBar value={50} className="custom-progress" />);
      const wrapper = document.querySelector(".custom-progress");
      expect(wrapper).toBeInTheDocument();
    });

    it("has rounded-full class on container", () => {
      render(<ProgressBar value={50} />);
      const container = document.querySelector(".rounded-full");
      expect(container).toBeInTheDocument();
    });

    it("has overflow-hidden class", () => {
      render(<ProgressBar value={50} />);
      const container = document.querySelector(".overflow-hidden");
      expect(container).toBeInTheDocument();
    });

    it("has transition class on fill", () => {
      render(<ProgressBar value={50} />);
      const progressFill = document.querySelector(".transition-all");
      expect(progressFill).toBeInTheDocument();
    });
  });
});
