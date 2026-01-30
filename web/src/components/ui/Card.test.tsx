import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Card, CardHeader, CardContent, CardFooter, CardVariant } from "./Card";

describe("Card", () => {
  describe("rendering", () => {
    it("renders children", () => {
      render(<Card>Card Content</Card>);
      expect(screen.getByText("Card Content")).toBeInTheDocument();
    });

    it("renders as div by default", () => {
      render(<Card>Content</Card>);
      const card = screen.getByText("Content").parentElement || screen.getByText("Content");
      expect(card.tagName).toBe("DIV");
    });

    it("renders as button when onClick provided", () => {
      render(<Card onClick={() => {}}>Clickable</Card>);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });
  });

  describe("variants", () => {
    const variants: CardVariant[] = ["default", "interactive", "selected"];

    variants.forEach((variant) => {
      it(`renders ${variant} variant`, () => {
        render(<Card variant={variant}>Content</Card>);
        expect(screen.getByText("Content")).toBeInTheDocument();
      });
    });

    it("uses default variant when not specified", () => {
      render(<Card>Default Card</Card>);
      expect(screen.getByText("Default Card")).toBeInTheDocument();
    });
  });

  describe("interactivity", () => {
    it("calls onClick when clicked", async () => {
      const handleClick = vi.fn();
      const user = userEvent.setup();

      render(<Card onClick={handleClick}>Click Me</Card>);

      await user.click(screen.getByRole("button"));
      expect(handleClick).toHaveBeenCalledTimes(1);
    });

    it("does not require onClick", () => {
      render(<Card>No Click</Card>);
      expect(screen.getByText("No Click")).toBeInTheDocument();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<Card className="custom-card">Styled</Card>);
      const card = screen.getByText("Styled").closest("div");
      expect(card).toHaveClass("custom-card");
    });

    it("has rounded-xl class", () => {
      render(<Card>Rounded</Card>);
      const card = screen.getByText("Rounded").closest("div");
      expect(card).toHaveClass("rounded-xl");
    });

    it("has border class", () => {
      render(<Card>Bordered</Card>);
      const card = screen.getByText("Bordered").closest("div");
      expect(card).toHaveClass("border");
    });
  });
});

describe("CardHeader", () => {
  it("renders children", () => {
    render(<CardHeader>Header Content</CardHeader>);
    expect(screen.getByText("Header Content")).toBeInTheDocument();
  });

  it("applies custom className", () => {
    render(<CardHeader className="custom-header">Header</CardHeader>);
    const header = screen.getByText("Header").closest("div");
    expect(header).toHaveClass("custom-header");
  });

  it("has border-b class", () => {
    render(<CardHeader>Header</CardHeader>);
    const header = screen.getByText("Header").closest("div");
    expect(header).toHaveClass("border-b");
  });

  it("has padding classes", () => {
    render(<CardHeader>Header</CardHeader>);
    const header = screen.getByText("Header").closest("div");
    expect(header).toHaveClass("px-4");
    expect(header).toHaveClass("py-3");
  });
});

describe("CardContent", () => {
  it("renders children", () => {
    render(<CardContent>Main Content</CardContent>);
    expect(screen.getByText("Main Content")).toBeInTheDocument();
  });

  it("applies custom className", () => {
    render(<CardContent className="custom-content">Content</CardContent>);
    const content = screen.getByText("Content").closest("div");
    expect(content).toHaveClass("custom-content");
  });

  it("has padding class", () => {
    render(<CardContent>Content</CardContent>);
    const content = screen.getByText("Content").closest("div");
    expect(content).toHaveClass("p-4");
  });
});

describe("CardFooter", () => {
  it("renders children", () => {
    render(<CardFooter>Footer Content</CardFooter>);
    expect(screen.getByText("Footer Content")).toBeInTheDocument();
  });

  it("applies custom className", () => {
    render(<CardFooter className="custom-footer">Footer</CardFooter>);
    const footer = screen.getByText("Footer").closest("div");
    expect(footer).toHaveClass("custom-footer");
  });

  it("has border-t class", () => {
    render(<CardFooter>Footer</CardFooter>);
    const footer = screen.getByText("Footer").closest("div");
    expect(footer).toHaveClass("border-t");
  });

  it("has padding classes", () => {
    render(<CardFooter>Footer</CardFooter>);
    const footer = screen.getByText("Footer").closest("div");
    expect(footer).toHaveClass("px-4");
    expect(footer).toHaveClass("py-3");
  });
});

describe("Card composition", () => {
  it("renders complete card with header, content, and footer", () => {
    render(
      <Card>
        <CardHeader>Title</CardHeader>
        <CardContent>Body</CardContent>
        <CardFooter>Actions</CardFooter>
      </Card>
    );

    expect(screen.getByText("Title")).toBeInTheDocument();
    expect(screen.getByText("Body")).toBeInTheDocument();
    expect(screen.getByText("Actions")).toBeInTheDocument();
  });
});
