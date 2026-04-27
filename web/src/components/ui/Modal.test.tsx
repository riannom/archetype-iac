import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal, ModalHeader, ModalFooter } from "./Modal";

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

    it("renders close button by default", () => {
      render(<Modal {...defaultProps} />);
      // Close button uses fa-xmark icon
      expect(document.querySelector("i.fa-xmark")).toBeInTheDocument();
    });

    it("hides close button when showCloseButton is false", () => {
      render(<Modal {...defaultProps} showCloseButton={false} title={undefined} />);
      expect(document.querySelector("i.fa-xmark")).not.toBeInTheDocument();
    });
  });

  describe("closing behavior", () => {
    it("calls onClose when close button is clicked", async () => {
      const onClose = vi.fn();
      const user = userEvent.setup();

      render(<Modal {...defaultProps} onClose={onClose} />);

      const closeButton = document.querySelector("i.fa-xmark")?.closest("button");
      if (closeButton) {
        await user.click(closeButton);
        expect(onClose).toHaveBeenCalledTimes(1);
      }
    });

    it("calls onClose when backdrop is clicked", async () => {
      const onClose = vi.fn();
      const user = userEvent.setup();

      render(<Modal {...defaultProps} onClose={onClose} />);

      // The backdrop is the first div with bg-black/50
      const backdrop = document.querySelector(".bg-black\\/50");
      if (backdrop) {
        await user.click(backdrop);
        expect(onClose).toHaveBeenCalledTimes(1);
      }
    });

    it("calls onClose when Escape key is pressed", async () => {
      const onClose = vi.fn();
      const user = userEvent.setup();

      render(<Modal {...defaultProps} onClose={onClose} />);

      await user.keyboard("{Escape}");
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  describe("sizes", () => {
    it("renders small size", () => {
      render(<Modal {...defaultProps} size="sm" />);
      expect(screen.getByText("Test Modal")).toBeInTheDocument();
    });

    it("renders medium size (default)", () => {
      render(<Modal {...defaultProps} />);
      expect(screen.getByText("Test Modal")).toBeInTheDocument();
    });

    it("renders large size", () => {
      render(<Modal {...defaultProps} size="lg" />);
      expect(screen.getByText("Test Modal")).toBeInTheDocument();
    });

    it("renders extra large size", () => {
      render(<Modal {...defaultProps} size="xl" />);
      expect(screen.getByText("Test Modal")).toBeInTheDocument();
    });

    it("renders full size", () => {
      render(<Modal {...defaultProps} size="full" />);
      expect(screen.getByText("Test Modal")).toBeInTheDocument();
    });
  });

  describe("scrolling", () => {
    it("prevents body scroll when open", () => {
      render(<Modal {...defaultProps} />);
      expect(document.body.style.overflow).toBe("hidden");
    });

    it("restores body scroll when closed", () => {
      const { rerender } = render(<Modal {...defaultProps} />);
      rerender(<Modal {...defaultProps} isOpen={false} />);
      expect(document.body.style.overflow).toBe("");
    });
  });

  describe("header omission", () => {
    it("omits the header when title and showCloseButton are both false/undefined", () => {
      const { container } = render(
        <Modal isOpen onClose={vi.fn()} showCloseButton={false}>
          body
        </Modal>,
      );
      // No header div with the border-b class should exist
      expect(container.querySelector("div.border-b")).toBeNull();
    });
  });
});

describe("ModalHeader", () => {
  it("renders children", () => {
    render(<ModalHeader>header content</ModalHeader>);
    expect(screen.getByText("header content")).toBeInTheDocument();
  });

  it("applies a custom className alongside the defaults", () => {
    const { container } = render(
      <ModalHeader className="extra">x</ModalHeader>,
    );
    expect(container.firstChild).toHaveClass("extra");
    expect(container.firstChild).toHaveClass("flex");
    expect(container.firstChild).toHaveClass("items-center");
  });
});

describe("ModalFooter", () => {
  it("renders children", () => {
    render(<ModalFooter>footer content</ModalFooter>);
    expect(screen.getByText("footer content")).toBeInTheDocument();
  });

  it("applies a custom className alongside the defaults", () => {
    const { container } = render(
      <ModalFooter className="extra">x</ModalFooter>,
    );
    expect(container.firstChild).toHaveClass("extra");
    expect(container.firstChild).toHaveClass("justify-end");
    expect(container.firstChild).toHaveClass("border-t");
  });
});
