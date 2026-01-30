import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DetailPopup from "./DetailPopup";

describe("DetailPopup", () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when isOpen is false", () => {
    const { container } = render(
      <DetailPopup isOpen={false} onClose={mockOnClose} title="Test Popup">
        <div>Content</div>
      </DetailPopup>
    );

    expect(container.firstChild).toBeNull();
  });

  it("renders popup when isOpen is true", () => {
    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test Popup">
        <div>Content</div>
      </DetailPopup>
    );

    expect(screen.getByText("Test Popup")).toBeInTheDocument();
    expect(screen.getByText("Content")).toBeInTheDocument();
  });

  it("displays the title correctly", () => {
    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Agent Details">
        <div>Content</div>
      </DetailPopup>
    );

    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Agent Details");
  });

  it("renders children content", () => {
    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
        <div data-testid="child-content">
          <p>Paragraph 1</p>
          <p>Paragraph 2</p>
        </div>
      </DetailPopup>
    );

    expect(screen.getByTestId("child-content")).toBeInTheDocument();
    expect(screen.getByText("Paragraph 1")).toBeInTheDocument();
    expect(screen.getByText("Paragraph 2")).toBeInTheDocument();
  });

  it("calls onClose when close button is clicked", async () => {
    const user = userEvent.setup();

    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
        <div>Content</div>
      </DetailPopup>
    );

    const closeButton = screen.getByRole("button");
    await user.click(closeButton);

    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when backdrop is clicked", async () => {
    const user = userEvent.setup();

    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
        <div>Content</div>
      </DetailPopup>
    );

    // The backdrop is the element with bg-black/50
    const backdrop = document.querySelector(".bg-black\\/50");
    expect(backdrop).toBeInTheDocument();

    if (backdrop) {
      await user.click(backdrop);
    }

    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  it("applies default width class when width prop is not provided", () => {
    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
        <div>Content</div>
      </DetailPopup>
    );

    // Find the dialog container (second child with bg-stone-50)
    const dialog = document.querySelector(".max-w-lg");
    expect(dialog).toBeInTheDocument();
  });

  it("applies custom width class when width prop is provided", () => {
    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test" width="max-w-2xl">
        <div>Content</div>
      </DetailPopup>
    );

    const dialog = document.querySelector(".max-w-2xl");
    expect(dialog).toBeInTheDocument();
  });

  it("has proper z-index for overlay", () => {
    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
        <div>Content</div>
      </DetailPopup>
    );

    const overlay = document.querySelector(".z-50");
    expect(overlay).toBeInTheDocument();
  });

  it("renders close icon button with correct styling", () => {
    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
        <div>Content</div>
      </DetailPopup>
    );

    const closeButton = screen.getByRole("button");
    expect(closeButton).toHaveClass("p-1", "text-stone-400");
  });

  it("has scrollable content area", () => {
    render(
      <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
        <div>Content</div>
      </DetailPopup>
    );

    const contentArea = document.querySelector(".overflow-y-auto");
    expect(contentArea).toBeInTheDocument();
  });

  describe("complex content scenarios", () => {
    it("renders nested components correctly", () => {
      const NestedComponent = () => <div data-testid="nested">Nested</div>;

      render(
        <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
          <NestedComponent />
        </DetailPopup>
      );

      expect(screen.getByTestId("nested")).toBeInTheDocument();
    });

    it("renders multiple children", () => {
      render(
        <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
          <div data-testid="child-1">First</div>
          <div data-testid="child-2">Second</div>
          <div data-testid="child-3">Third</div>
        </DetailPopup>
      );

      expect(screen.getByTestId("child-1")).toBeInTheDocument();
      expect(screen.getByTestId("child-2")).toBeInTheDocument();
      expect(screen.getByTestId("child-3")).toBeInTheDocument();
    });
  });

  describe("accessibility", () => {
    it("contains a heading element for the title", () => {
      render(
        <DetailPopup isOpen={true} onClose={mockOnClose} title="Accessible Title">
          <div>Content</div>
        </DetailPopup>
      );

      const heading = screen.getByRole("heading", { level: 2 });
      expect(heading).toBeInTheDocument();
      expect(heading).toHaveTextContent("Accessible Title");
    });

    it("close button is focusable", () => {
      render(
        <DetailPopup isOpen={true} onClose={mockOnClose} title="Test">
          <div>Content</div>
        </DetailPopup>
      );

      const closeButton = screen.getByRole("button");
      closeButton.focus();
      expect(document.activeElement).toBe(closeButton);
    });
  });
});
