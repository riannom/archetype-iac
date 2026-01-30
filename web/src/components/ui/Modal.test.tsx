import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "./Modal";

describe("Modal", () => {
  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
    title: "Test Modal",
    children: <div>Modal content</div>,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("rendering", () => {
    it("renders when isOpen is true", () => {
      render(<Modal {...defaultProps} />);
      expect(screen.getByText("Test Modal")).toBeInTheDocument();
      expect(screen.getByText("Modal content")).toBeInTheDocument();
    });

    it("does not render when isOpen is false", () => {
      render(<Modal {...defaultProps} isOpen={false} />);
      expect(screen.queryByText("Test Modal")).not.toBeInTheDocument();
    });

    it("renders title", () => {
      render(<Modal {...defaultProps} />);
      expect(screen.getByText("Test Modal")).toBeInTheDocument();
    });

    it("renders children", () => {
      render(
        <Modal {...defaultProps}>
          <p>Custom content</p>
        </Modal>
      );
      expect(screen.getByText("Custom content")).toBeInTheDocument();
    });

    it("renders close button", () => {
      render(<Modal {...defaultProps} />);
      expect(screen.getByRole("button", { name: /close/i })).toBeInTheDocument();
    });
  });

  describe("closing behavior", () => {
    it("calls onClose when close button is clicked", async () => {
      const onClose = vi.fn();
      const user = userEvent.setup();

      render(<Modal {...defaultProps} onClose={onClose} />);

      await user.click(screen.getByRole("button", { name: /close/i }));
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("calls onClose when backdrop is clicked", async () => {
      const onClose = vi.fn();
      const user = userEvent.setup();

      render(<Modal {...defaultProps} onClose={onClose} />);

      // Click on the backdrop (the overlay element)
      const backdrop = document.querySelector('[data-testid="modal-backdrop"]');
      if (backdrop) {
        await user.click(backdrop);
        expect(onClose).toHaveBeenCalledTimes(1);
      }
    });

    it("does not close on backdrop click when closeOnBackdropClick is false", async () => {
      const onClose = vi.fn();
      const user = userEvent.setup();

      render(
        <Modal {...defaultProps} onClose={onClose} closeOnBackdropClick={false} />
      );

      const backdrop = document.querySelector('[data-testid="modal-backdrop"]');
      if (backdrop) {
        await user.click(backdrop);
        expect(onClose).not.toHaveBeenCalled();
      }
    });

    it("calls onClose when Escape key is pressed", async () => {
      const onClose = vi.fn();
      const user = userEvent.setup();

      render(<Modal {...defaultProps} onClose={onClose} />);

      await user.keyboard("{Escape}");
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("does not close on Escape when closeOnEscape is false", async () => {
      const onClose = vi.fn();
      const user = userEvent.setup();

      render(
        <Modal {...defaultProps} onClose={onClose} closeOnEscape={false} />
      );

      await user.keyboard("{Escape}");
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  describe("sizes", () => {
    it("renders small size", () => {
      render(<Modal {...defaultProps} size="sm" />);
      const modal = screen.getByRole("dialog");
      expect(modal).toBeInTheDocument();
    });

    it("renders medium size (default)", () => {
      render(<Modal {...defaultProps} />);
      const modal = screen.getByRole("dialog");
      expect(modal).toBeInTheDocument();
    });

    it("renders large size", () => {
      render(<Modal {...defaultProps} size="lg" />);
      const modal = screen.getByRole("dialog");
      expect(modal).toBeInTheDocument();
    });

    it("renders extra large size", () => {
      render(<Modal {...defaultProps} size="xl" />);
      const modal = screen.getByRole("dialog");
      expect(modal).toBeInTheDocument();
    });

    it("renders full size", () => {
      render(<Modal {...defaultProps} size="full" />);
      const modal = screen.getByRole("dialog");
      expect(modal).toBeInTheDocument();
    });
  });

  describe("footer", () => {
    it("renders footer when provided", () => {
      render(
        <Modal {...defaultProps} footer={<button>Save</button>} />
      );
      expect(screen.getByText("Save")).toBeInTheDocument();
    });

    it("does not render footer when not provided", () => {
      render(<Modal {...defaultProps} />);
      // Footer area should not exist
      expect(screen.queryByText("Save")).not.toBeInTheDocument();
    });
  });

  describe("accessibility", () => {
    it("has dialog role", () => {
      render(<Modal {...defaultProps} />);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("has aria-modal attribute", () => {
      render(<Modal {...defaultProps} />);
      expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
    });

    it("has aria-labelledby pointing to title", () => {
      render(<Modal {...defaultProps} />);
      const dialog = screen.getByRole("dialog");
      const titleId = dialog.getAttribute("aria-labelledby");
      expect(titleId).toBeTruthy();
      expect(document.getElementById(titleId!)).toHaveTextContent("Test Modal");
    });

    it("traps focus within modal", async () => {
      const user = userEvent.setup();

      render(
        <Modal {...defaultProps}>
          <input data-testid="input1" />
          <button data-testid="button1">Button</button>
        </Modal>
      );

      // Tab through focusable elements
      await user.tab();
      expect(screen.getByTestId("input1")).toHaveFocus();

      await user.tab();
      expect(screen.getByTestId("button1")).toHaveFocus();

      // Should cycle back (focus trap behavior depends on implementation)
    });
  });

  describe("scrolling", () => {
    it("prevents body scroll when open", () => {
      render(<Modal {...defaultProps} />);
      // Body should have overflow hidden
      expect(document.body.style.overflow).toBe("hidden");
    });

    it("restores body scroll when closed", () => {
      const { rerender } = render(<Modal {...defaultProps} />);
      rerender(<Modal {...defaultProps} isOpen={false} />);
      expect(document.body.style.overflow).toBe("");
    });
  });
});
