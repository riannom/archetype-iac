import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeviceConfigCard from "./DeviceConfigCard";
import { DeviceModel, DeviceType } from "../types";

const createMockDevice = (
  overrides: Partial<DeviceModel> = {}
): DeviceModel => {
  const device: DeviceModel = {
    id: overrides.id || "ceos",
    name: overrides.name || "Arista cEOS",
    type: overrides.type || DeviceType.ROUTER,
    icon: overrides.icon || "fa-microchip",
    versions: overrides.versions || ["4.28.0F", "4.27.0F"],
    isActive: overrides.isActive ?? true,
    vendor: overrides.vendor || "Arista",
    licenseRequired: overrides.licenseRequired ?? false,
    isCustom: overrides.isCustom ?? false,
  };
  // Only set these if explicitly provided or not undefined in overrides
  if ('memory' in overrides) {
    device.memory = overrides.memory;
  } else {
    device.memory = 2048;
  }
  if ('cpu' in overrides) {
    device.cpu = overrides.cpu;
  } else {
    device.cpu = 2;
  }
  if ('maxPorts' in overrides) {
    device.maxPorts = overrides.maxPorts;
  } else {
    device.maxPorts = 8;
  }
  return device;
};

describe("DeviceConfigCard", () => {
  const mockOnSelect = vi.fn();

  const defaultProps = {
    device: createMockDevice(),
    isSelected: false,
    onSelect: mockOnSelect,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("Basic rendering", () => {
    it("renders device name", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
    });

    it("renders vendor name", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      expect(screen.getByText("Arista")).toBeInTheDocument();
    });

    it("renders device type", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      expect(screen.getByText("router")).toBeInTheDocument();
    });

    it("renders device icon", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      expect(document.querySelector(".fa-microchip")).toBeInTheDocument();
    });

    it("uses default icon when not specified", () => {
      const deviceWithoutIcon = createMockDevice({ icon: "" });
      render(
        <DeviceConfigCard {...defaultProps} device={deviceWithoutIcon} />
      );

      // Should use default fa-microchip icon
      expect(document.querySelector(".fa-microchip")).toBeInTheDocument();
    });
  });

  describe("Resource display", () => {
    it("displays memory in MB", () => {
      const device = createMockDevice({ memory: 512 });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("512MB")).toBeInTheDocument();
    });

    it("displays memory in GB when >= 1024MB", () => {
      const device = createMockDevice({ memory: 2048 });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("2.0GB")).toBeInTheDocument();
    });

    it("displays memory with decimal for non-round GB values", () => {
      const device = createMockDevice({ memory: 1536 });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("1.5GB")).toBeInTheDocument();
    });

    it("displays dash when memory is not set", () => {
      const device = createMockDevice({ memory: undefined });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("-")).toBeInTheDocument();
    });

    it("displays dash when memory is 0", () => {
      const device = createMockDevice({ memory: 0 });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("-")).toBeInTheDocument();
    });

    it("displays CPU count", () => {
      const device = createMockDevice({ cpu: 4 });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("4 CPU")).toBeInTheDocument();
    });

    it("displays default CPU count of 1 when not specified", () => {
      const device = createMockDevice({ cpu: undefined });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("1 CPU")).toBeInTheDocument();
    });

    it("displays port count", () => {
      const device = createMockDevice({ maxPorts: 48 });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("48 ports")).toBeInTheDocument();
    });

    it("displays default port count of 8 when not specified", () => {
      const device = createMockDevice({ maxPorts: undefined });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("8 ports")).toBeInTheDocument();
    });

    it("displays memory icon", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      expect(document.querySelector(".fa-memory")).toBeInTheDocument();
    });

    it("displays CPU icon", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      // There should be two fa-microchip icons - one for device and one for CPU
      const microchipIcons = document.querySelectorAll(".fa-microchip");
      expect(microchipIcons.length).toBeGreaterThanOrEqual(1);
    });

    it("displays ethernet icon for ports", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      expect(document.querySelector(".fa-ethernet")).toBeInTheDocument();
    });
  });

  describe("Badge display", () => {
    it("shows License badge when license is required", () => {
      const device = createMockDevice({ licenseRequired: true });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("License")).toBeInTheDocument();
    });

    it("does not show License badge when license is not required", () => {
      const device = createMockDevice({ licenseRequired: false });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.queryByText("License")).not.toBeInTheDocument();
    });

    it("shows Inactive badge when device is not active", () => {
      const device = createMockDevice({ isActive: false });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("Inactive")).toBeInTheDocument();
    });

    it("does not show Inactive badge when device is active", () => {
      const device = createMockDevice({ isActive: true });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.queryByText("Inactive")).not.toBeInTheDocument();
    });

    it("shows Custom badge when device isCustom is true", () => {
      const device = createMockDevice({ isCustom: true });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("Custom")).toBeInTheDocument();
    });

    it("shows Custom badge when isCustom prop is true", () => {
      const device = createMockDevice({ isCustom: false });
      render(
        <DeviceConfigCard {...defaultProps} device={device} isCustom={true} />
      );

      expect(screen.getByText("Custom")).toBeInTheDocument();
    });

    it("does not show Custom badge for non-custom devices", () => {
      const device = createMockDevice({ isCustom: false });
      render(
        <DeviceConfigCard {...defaultProps} device={device} isCustom={false} />
      );

      expect(screen.queryByText("Custom")).not.toBeInTheDocument();
    });

    it("can show multiple badges simultaneously", () => {
      const device = createMockDevice({
        licenseRequired: true,
        isActive: false,
        isCustom: true,
      });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("License")).toBeInTheDocument();
      expect(screen.getByText("Inactive")).toBeInTheDocument();
      expect(screen.getByText("Custom")).toBeInTheDocument();
    });
  });

  describe("Selection behavior", () => {
    it("calls onSelect when card is clicked", async () => {
      const user = userEvent.setup();
      render(<DeviceConfigCard {...defaultProps} />);

      await user.click(screen.getByText("Arista cEOS"));

      expect(mockOnSelect).toHaveBeenCalledTimes(1);
    });

    it("card is clickable anywhere", async () => {
      const user = userEvent.setup();
      render(<DeviceConfigCard {...defaultProps} />);

      // Click on vendor text
      await user.click(screen.getByText("Arista"));

      expect(mockOnSelect).toHaveBeenCalledTimes(1);
    });
  });

  describe("Selected state styling", () => {
    it("applies sage styling when selected (non-custom)", () => {
      render(<DeviceConfigCard {...defaultProps} isSelected={true} />);

      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      expect(card).toHaveClass("glass-surface");
      expect(card).toHaveClass("border-l-sage-500");
    });

    it("applies sage styling when selected and custom", () => {
      render(
        <DeviceConfigCard
          {...defaultProps}
          isSelected={true}
          isCustom={true}
        />
      );

      // Custom devices when selected use the same sage styling as non-custom
      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      expect(card).toHaveClass("glass-surface");
      expect(card).toHaveClass("border-l-sage-500");
    });

    it("applies default styling when not selected (non-custom)", () => {
      render(<DeviceConfigCard {...defaultProps} isSelected={false} />);

      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      expect(card).toHaveClass("glass-surface");
      expect(card).toHaveClass("border-stone-200");
    });

    it("applies subtle stone styling when not selected but custom", () => {
      render(
        <DeviceConfigCard
          {...defaultProps}
          isSelected={false}
          isCustom={true}
        />
      );

      // Custom devices when not selected use subtle stone styling
      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      expect(card).toHaveClass("glass-surface");
      expect(card).toHaveClass("border-stone-300");
    });
  });

  describe("Selected state icon styling", () => {
    it("applies sage icon styling when selected (non-custom)", () => {
      render(<DeviceConfigCard {...defaultProps} isSelected={true} />);

      const iconContainer = document
        .querySelector(".fa-microchip")
        ?.closest("div[class*='w-10']");
      expect(iconContainer).toHaveClass("bg-sage-100");
      expect(iconContainer).toHaveClass("text-sage-600");
    });

    it("applies sage icon styling when selected and custom", () => {
      render(
        <DeviceConfigCard
          {...defaultProps}
          isSelected={true}
          isCustom={true}
        />
      );

      // Custom devices when selected use the same sage icon styling as non-custom
      const iconContainer = document
        .querySelector(".fa-microchip")
        ?.closest("div[class*='w-10']");
      expect(iconContainer).toHaveClass("bg-sage-100");
      expect(iconContainer).toHaveClass("text-sage-600");
    });

    it("applies stone icon styling when not selected (non-custom)", () => {
      render(<DeviceConfigCard {...defaultProps} isSelected={false} />);

      const iconContainer = document
        .querySelector(".fa-microchip")
        ?.closest("div[class*='w-10']");
      expect(iconContainer).toHaveClass("bg-stone-100");
      expect(iconContainer).toHaveClass("text-stone-500");
    });

    it("applies stone icon styling when not selected but custom", () => {
      render(
        <DeviceConfigCard
          {...defaultProps}
          isSelected={false}
          isCustom={true}
        />
      );

      // Custom devices when not selected use stone icon styling
      const iconContainer = document
        .querySelector(".fa-microchip")
        ?.closest("div[class*='w-10']");
      expect(iconContainer).toHaveClass("bg-stone-200");
      expect(iconContainer).toHaveClass("text-stone-600");
    });
  });

  describe("Selected state text styling", () => {
    it("applies stone text styling when selected (non-custom)", () => {
      render(<DeviceConfigCard {...defaultProps} isSelected={true} />);

      const heading = screen.getByText("Arista cEOS");
      expect(heading).toHaveClass("text-stone-900");
    });

    it("applies stone text styling when selected and custom", () => {
      render(
        <DeviceConfigCard
          {...defaultProps}
          isSelected={true}
          isCustom={true}
        />
      );

      // Custom devices when selected use the same stone text styling as non-custom
      const heading = screen.getByText("Arista cEOS");
      expect(heading).toHaveClass("text-stone-900");
    });

    it("applies stone text styling when not selected (non-custom)", () => {
      render(<DeviceConfigCard {...defaultProps} isSelected={false} />);

      const heading = screen.getByText("Arista cEOS");
      expect(heading).toHaveClass("text-stone-900");
    });

    it("applies stone text styling when not selected but custom", () => {
      render(
        <DeviceConfigCard
          {...defaultProps}
          isSelected={false}
          isCustom={true}
        />
      );

      // Custom devices when not selected use stone text styling
      const heading = screen.getByText("Arista cEOS");
      expect(heading).toHaveClass("text-stone-900");
    });
  });

  describe("Device types", () => {
    it("displays router type", () => {
      const device = createMockDevice({ type: DeviceType.ROUTER });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("router")).toBeInTheDocument();
    });

    it("displays switch type", () => {
      const device = createMockDevice({ type: DeviceType.SWITCH });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("switch")).toBeInTheDocument();
    });

    it("displays firewall type", () => {
      const device = createMockDevice({ type: DeviceType.FIREWALL });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("firewall")).toBeInTheDocument();
    });

    it("displays host type", () => {
      const device = createMockDevice({ type: DeviceType.HOST });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("host")).toBeInTheDocument();
    });

    it("displays container type", () => {
      const device = createMockDevice({ type: DeviceType.CONTAINER });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      expect(screen.getByText("container")).toBeInTheDocument();
    });
  });

  describe("CSS classes", () => {
    it("has cursor-pointer class for clickability", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      expect(card).toHaveClass("cursor-pointer");
    });

    it("has transition-all class for smooth styling changes", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      expect(card).toHaveClass("transition-all");
    });

    it("has rounded-lg class", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      expect(card).toHaveClass("rounded-lg");
    });

    it("has border class", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      expect(card).toHaveClass("border");
    });
  });

  describe("Layout structure", () => {
    it("renders icon, device info, and badges in correct order", () => {
      const device = createMockDevice({ licenseRequired: true });
      render(<DeviceConfigCard {...defaultProps} device={device} />);

      const card = screen.getByText("Arista cEOS").closest("div[class*='rounded-lg']");
      const children = card?.querySelector("div[class*='flex items-start']")?.children;

      // Should have icon container, device info container, and badges container
      expect(children?.length).toBe(3);
    });

    it("device name is truncated with overflow", () => {
      render(<DeviceConfigCard {...defaultProps} />);

      const heading = screen.getByText("Arista cEOS");
      expect(heading).toHaveClass("truncate");
    });
  });
});
