import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import VendorOptionsPanel from "./VendorOptionsPanel";

describe("VendorOptionsPanel", () => {
  const mockOnChange = vi.fn();

  const defaultProps = {
    deviceId: "generic-device",
    vendorName: "Generic",
    options: {},
    baseOptions: {},
    overriddenOptions: {},
    onChange: mockOnChange,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("Empty options state", () => {
    it("shows 'No vendor-specific options available' when options are empty", () => {
      render(<VendorOptionsPanel {...defaultProps} />);

      expect(
        screen.getByText("No vendor-specific options available for this device.")
      ).toBeInTheDocument();
    });

    it("displays italic text for empty options message", () => {
      render(<VendorOptionsPanel {...defaultProps} />);

      const message = screen.getByText(
        "No vendor-specific options available for this device."
      );
      expect(message).toHaveClass("italic");
    });
  });

  describe("Arista cEOS options", () => {
    it("renders ZTP cancel option for eos device", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="eos"
          vendorName="Arista"
          options={{ zerotouchCancel: true }}
        />
      );

      expect(
        screen.getByText("Zero Touch Provisioning Cancel")
      ).toBeInTheDocument();
    });

    it("renders ZTP cancel option for ceos device", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="ceos"
          vendorName="Arista"
          options={{ zerotouchCancel: true }}
        />
      );

      expect(
        screen.getByText("Zero Touch Provisioning Cancel")
      ).toBeInTheDocument();
    });

    it("renders ZTP cancel option for arista-based device ID", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="arista-ceos-custom"
          vendorName="Arista"
          options={{ zerotouchCancel: false }}
        />
      );

      expect(
        screen.getByText("Zero Touch Provisioning Cancel")
      ).toBeInTheDocument();
    });

    it("displays ZTP description", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="eos"
          vendorName="Arista"
          options={{ zerotouchCancel: true }}
        />
      );

      expect(
        screen.getByText(
          "Automatically cancel ZTP on boot to prevent boot delays in isolated lab environments"
        )
      ).toBeInTheDocument();
    });

    it("calls onChange when ZTP toggle is clicked", async () => {
      const user = userEvent.setup();
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="eos"
          vendorName="Arista"
          options={{ zerotouchCancel: true }}
        />
      );

      const toggleButton = screen
        .getByText("Zero Touch Provisioning Cancel")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button");

      await user.click(toggleButton!);

      expect(mockOnChange).toHaveBeenCalledWith("zerotouchCancel", false);
    });

    it("defaults zerotouchCancel to true when not specified", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="eos"
          vendorName="Arista"
          options={{}}
        />
      );

      // Toggle should be in "on" position (bg-sage-600 class)
      const toggleButton = screen
        .getByText("Zero Touch Provisioning Cancel")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button");

      expect(toggleButton).toHaveClass("bg-sage-600");
    });
  });

  describe("Nokia SR Linux options", () => {
    it("renders gNMI option for srlinux device", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="srlinux"
          vendorName="Nokia"
          options={{ gnmiEnabled: true }}
        />
      );

      expect(screen.getByText("gNMI Interface")).toBeInTheDocument();
    });

    it("renders gNMI option for nokia_srlinux device", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="nokia_srlinux"
          vendorName="Nokia"
          options={{ gnmiEnabled: true }}
        />
      );

      expect(screen.getByText("gNMI Interface")).toBeInTheDocument();
    });

    it("renders gNMI option for Nokia vendor name", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="some-device"
          vendorName="Nokia"
          options={{ gnmiEnabled: false }}
        />
      );

      expect(screen.getByText("gNMI Interface")).toBeInTheDocument();
    });

    it("displays gNMI description", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="srlinux"
          vendorName="Nokia"
          options={{ gnmiEnabled: true }}
        />
      );

      expect(
        screen.getByText(
          "Enable gNMI management interface for programmatic configuration"
        )
      ).toBeInTheDocument();
    });

    it("calls onChange when gNMI toggle is clicked", async () => {
      const user = userEvent.setup();
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="srlinux"
          vendorName="Nokia"
          options={{ gnmiEnabled: true }}
        />
      );

      const toggleButton = screen
        .getByText("gNMI Interface")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button");

      await user.click(toggleButton!);

      expect(mockOnChange).toHaveBeenCalledWith("gnmiEnabled", false);
    });

    it("defaults gnmiEnabled to true when not specified", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="srlinux"
          vendorName="Nokia"
          options={{}}
        />
      );

      const toggleButton = screen
        .getByText("gNMI Interface")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button");

      expect(toggleButton).toHaveClass("bg-sage-600");
    });
  });

  describe("Generic options display", () => {
    it("renders boolean options as toggles", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ customOption: true }}
        />
      );

      expect(screen.getByText("Custom Option")).toBeInTheDocument();
    });

    it("renders non-boolean options as text display", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ stringOption: "some-value" }}
        />
      );

      expect(screen.getByText("String Option")).toBeInTheDocument();
      expect(screen.getByText("some-value")).toBeInTheDocument();
    });

    it("renders number options as text display", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ numberOption: 42 }}
        />
      );

      expect(screen.getByText("Number Option")).toBeInTheDocument();
      expect(screen.getByText("42")).toBeInTheDocument();
    });

    it("calls onChange when generic boolean option is toggled", async () => {
      const user = userEvent.setup();
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ customBool: false }}
        />
      );

      const toggleButton = screen
        .getByText("Custom Bool")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button");

      await user.click(toggleButton!);

      expect(mockOnChange).toHaveBeenCalledWith("customBool", true);
    });

    it("formats camelCase keys to readable labels", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ myCustomOption: true }}
        />
      );

      expect(screen.getByText("My Custom Option")).toBeInTheDocument();
    });

    it("formats snake_case keys to readable labels", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ my_custom_option: true }}
        />
      );

      expect(screen.getByText("My Custom Option")).toBeInTheDocument();
    });

    it("displays multiple options", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{
            option1: true,
            option2: false,
            option3: "value",
          }}
        />
      );

      expect(screen.getByText("Option1")).toBeInTheDocument();
      expect(screen.getByText("Option2")).toBeInTheDocument();
      expect(screen.getByText("Option3")).toBeInTheDocument();
    });
  });

  describe("Override indicator", () => {
    it("shows override indicator for overridden options", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ customOption: true }}
          overriddenOptions={{ customOption: true }}
        />
      );

      // Blue dot indicator for override
      const indicator = document.querySelector(".bg-blue-500");
      expect(indicator).toBeInTheDocument();
    });

    it("does not show override indicator for non-overridden options", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ customOption: true }}
          overriddenOptions={{}}
        />
      );

      // Should not have blue dot indicator
      const indicator = document.querySelector(".bg-blue-500");
      expect(indicator).not.toBeInTheDocument();
    });

    it("shows override indicator for Arista ZTP option when overridden", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          deviceId="eos"
          vendorName="Arista"
          options={{ zerotouchCancel: false }}
          overriddenOptions={{ zerotouchCancel: false }}
        />
      );

      const indicator = document.querySelector(".bg-blue-500");
      expect(indicator).toBeInTheDocument();
    });

    it("shows override indicator for non-boolean overridden options", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ stringOption: "custom-value" }}
          overriddenOptions={{ stringOption: "custom-value" }}
        />
      );

      const indicator = document.querySelector(".bg-blue-500");
      expect(indicator).toBeInTheDocument();
    });

    it("has correct title attribute on override indicator", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ customOption: true }}
          overriddenOptions={{ customOption: true }}
        />
      );

      const indicator = document.querySelector(".bg-blue-500");
      expect(indicator).toHaveAttribute("title", "Custom override");
    });
  });

  describe("Toggle button styling", () => {
    it("applies sage color when toggle is on", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ testOption: true }}
        />
      );

      const toggleButton = screen
        .getByText("Test Option")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button");

      expect(toggleButton).toHaveClass("bg-sage-600");
    });

    it("applies stone color when toggle is off", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ testOption: false }}
        />
      );

      const toggleButton = screen
        .getByText("Test Option")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button");

      expect(toggleButton).toHaveClass("bg-stone-300");
    });

    it("toggle knob moves position based on value", () => {
      const { rerender } = render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ testOption: false }}
        />
      );

      let knob = screen
        .getByText("Test Option")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button span");

      expect(knob).toHaveClass("left-0.5");

      rerender(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ testOption: true }}
        />
      );

      knob = screen
        .getByText("Test Option")
        .closest("div[class*='flex items-start']")
        ?.querySelector("button span");

      expect(knob).toHaveClass("left-5");
    });
  });

  describe("Layout and styling", () => {
    it("renders options with border-b styling", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ option1: true }}
        />
      );

      const optionRow = screen
        .getByText("Option1")
        .closest("div[class*='border-b']");
      expect(optionRow).toBeInTheDocument();
    });

    it("renders non-boolean options in monospace font", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ stringOption: "test-value" }}
        />
      );

      const value = screen.getByText("test-value");
      expect(value).toHaveClass("font-mono");
    });

    it("renders option descriptions in smaller text", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ testOption: true }}
        />
      );

      const description = screen.getByText("Configure testOption setting");
      expect(description).toHaveClass("text-[10px]");
    });
  });

  describe("Edge cases", () => {
    it("handles empty string option values", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ emptyString: "" }}
        />
      );

      // Empty string should still render the label
      expect(screen.getByText("Empty String")).toBeInTheDocument();
    });

    it("handles null-ish values converted to string", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ nullValue: null as unknown as string }}
        />
      );

      expect(screen.getByText("null")).toBeInTheDocument();
    });

    it("handles object values converted to string", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ objectValue: { key: "value" } as unknown as string }}
        />
      );

      expect(screen.getByText("[object Object]")).toBeInTheDocument();
    });

    it("handles array values converted to string", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ arrayValue: ["a", "b"] as unknown as string }}
        />
      );

      expect(screen.getByText("a,b")).toBeInTheDocument();
    });
  });

  describe("formatOptionLabel utility", () => {
    it("capitalizes first letter of each word", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ lowercase: true }}
        />
      );

      expect(screen.getByText("Lowercase")).toBeInTheDocument();
    });

    it("handles already capitalized keys", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ AlreadyCapitalized: true }}
        />
      );

      expect(screen.getByText("Already Capitalized")).toBeInTheDocument();
    });

    it("handles mixed case keys", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ mixedCaseKey: true }}
        />
      );

      expect(screen.getByText("Mixed Case Key")).toBeInTheDocument();
    });

    it("handles consecutive capital letters", () => {
      render(
        <VendorOptionsPanel
          {...defaultProps}
          options={{ enableHTTPServer: true }}
        />
      );

      // Each capital letter becomes a word break
      expect(screen.getByText("Enable H T T P Server")).toBeInTheDocument();
    });
  });
});
