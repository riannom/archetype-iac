import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Input, Textarea, InputSize } from "./Input";

describe("Input", () => {
  describe("rendering", () => {
    it("renders input element", () => {
      render(<Input placeholder="Enter text" />);
      expect(screen.getByPlaceholderText("Enter text")).toBeInTheDocument();
    });

    it("renders label when provided", () => {
      render(<Input label="Username" />);
      expect(screen.getByText("Username")).toBeInTheDocument();
    });

    it("associates label with input", () => {
      render(<Input label="Email" />);
      const input = screen.getByLabelText("Email");
      expect(input).toBeInTheDocument();
    });
  });

  describe("sizes", () => {
    const sizes: InputSize[] = ["sm", "md", "lg"];

    sizes.forEach((size) => {
      it(`renders ${size} size`, () => {
        render(<Input size={size} placeholder={`${size} input`} />);
        expect(screen.getByPlaceholderText(`${size} input`)).toBeInTheDocument();
      });
    });

    it("uses md size by default", () => {
      render(<Input placeholder="default" />);
      expect(screen.getByPlaceholderText("default")).toBeInTheDocument();
    });
  });

  describe("states", () => {
    it("applies disabled state", () => {
      render(<Input disabled placeholder="disabled" />);
      expect(screen.getByPlaceholderText("disabled")).toBeDisabled();
    });

    it("shows error message when error provided", () => {
      render(<Input error="This field is required" />);
      expect(screen.getByText("This field is required")).toBeInTheDocument();
    });

    it("shows hint when provided and no error", () => {
      render(<Input hint="Enter your full name" />);
      expect(screen.getByText("Enter your full name")).toBeInTheDocument();
    });

    it("hides hint when error is shown", () => {
      render(<Input hint="Hint text" error="Error text" />);
      expect(screen.queryByText("Hint text")).not.toBeInTheDocument();
      expect(screen.getByText("Error text")).toBeInTheDocument();
    });
  });

  describe("icons", () => {
    it("renders left icon", () => {
      render(<Input leftIcon="fa-solid fa-search" />);
      const icon = document.querySelector("i.fa-search");
      expect(icon).toBeInTheDocument();
    });

    it("renders right icon", () => {
      render(<Input rightIcon="fa-solid fa-check" />);
      const icon = document.querySelector("i.fa-check");
      expect(icon).toBeInTheDocument();
    });
  });

  describe("interaction", () => {
    it("accepts user input", async () => {
      const user = userEvent.setup();
      render(<Input placeholder="type here" />);

      const input = screen.getByPlaceholderText("type here");
      await user.type(input, "Hello World");

      expect(input).toHaveValue("Hello World");
    });

    it("calls onChange when value changes", async () => {
      const handleChange = vi.fn();
      const user = userEvent.setup();
      render(<Input placeholder="input" onChange={handleChange} />);

      await user.type(screen.getByPlaceholderText("input"), "a");

      expect(handleChange).toHaveBeenCalled();
    });

    it("does not accept input when disabled", async () => {
      const user = userEvent.setup();
      render(<Input disabled placeholder="disabled" />);

      const input = screen.getByPlaceholderText("disabled");
      await user.type(input, "test");

      expect(input).toHaveValue("");
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<Input className="custom-input" placeholder="styled" />);
      const input = screen.getByPlaceholderText("styled");
      expect(input).toHaveClass("custom-input");
    });

    it("has rounded-lg class", () => {
      render(<Input placeholder="rounded" />);
      const input = screen.getByPlaceholderText("rounded");
      expect(input).toHaveClass("rounded-lg");
    });
  });

  describe("accessibility", () => {
    it("can be focused", () => {
      render(<Input placeholder="focusable" />);
      const input = screen.getByPlaceholderText("focusable");
      input.focus();
      expect(input).toHaveFocus();
    });

    it("supports custom id", () => {
      render(<Input id="custom-id" placeholder="with id" />);
      expect(document.getElementById("custom-id")).toBeInTheDocument();
    });
  });
});

describe("Textarea", () => {
  describe("rendering", () => {
    it("renders textarea element", () => {
      render(<Textarea placeholder="Enter description" />);
      expect(screen.getByPlaceholderText("Enter description")).toBeInTheDocument();
    });

    it("renders label when provided", () => {
      render(<Textarea label="Description" />);
      expect(screen.getByText("Description")).toBeInTheDocument();
    });

    it("associates label with textarea", () => {
      render(<Textarea label="Notes" />);
      const textarea = screen.getByLabelText("Notes");
      expect(textarea).toBeInTheDocument();
    });
  });

  describe("states", () => {
    it("applies disabled state", () => {
      render(<Textarea disabled placeholder="disabled" />);
      expect(screen.getByPlaceholderText("disabled")).toBeDisabled();
    });

    it("shows error message when error provided", () => {
      render(<Textarea error="Description is required" />);
      expect(screen.getByText("Description is required")).toBeInTheDocument();
    });

    it("shows hint when provided and no error", () => {
      render(<Textarea hint="Max 500 characters" />);
      expect(screen.getByText("Max 500 characters")).toBeInTheDocument();
    });
  });

  describe("interaction", () => {
    it("accepts user input", async () => {
      const user = userEvent.setup();
      render(<Textarea placeholder="type here" />);

      const textarea = screen.getByPlaceholderText("type here");
      await user.type(textarea, "Multi\nLine\nText");

      expect(textarea).toHaveValue("Multi\nLine\nText");
    });

    it("calls onChange when value changes", async () => {
      const handleChange = vi.fn();
      const user = userEvent.setup();
      render(<Textarea placeholder="textarea" onChange={handleChange} />);

      await user.type(screen.getByPlaceholderText("textarea"), "a");

      expect(handleChange).toHaveBeenCalled();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<Textarea className="custom-textarea" placeholder="styled" />);
      const textarea = screen.getByPlaceholderText("styled");
      expect(textarea).toHaveClass("custom-textarea");
    });

    it("has resize-none class", () => {
      render(<Textarea placeholder="no-resize" />);
      const textarea = screen.getByPlaceholderText("no-resize");
      expect(textarea).toHaveClass("resize-none");
    });
  });
});
