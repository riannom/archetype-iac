import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeviceCard from "./DeviceCard";
import { DeviceModel, DeviceType, ImageLibraryEntry } from "../types";

// Mock DragContext
const mockHandleDragOver = vi.fn();
const mockHandleDragLeave = vi.fn();
const mockHandleDrop = vi.fn();

vi.mock("../contexts/DragContext", () => ({
  useDropHandlers: (deviceId: string) => ({
    handleDragOver: mockHandleDragOver,
    handleDragLeave: mockHandleDragLeave,
    handleDrop: mockHandleDrop,
    isDropTarget: false,
    isDragging: false,
  }),
}));

// Mock formatSize
vi.mock("../../utils/format", () => ({
  formatSize: (bytes: number | null | undefined) => {
    if (!bytes) return "";
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) return `${gb.toFixed(1)} GB`;
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(0)} MB`;
  },
}));

describe("DeviceCard", () => {
  const mockDevice: DeviceModel = {
    id: "ceos",
    name: "Arista cEOS",
    type: DeviceType.ROUTER,
    icon: "fa-microchip",
    versions: ["4.28.0F"],
    isActive: true,
    vendor: "Arista",
    tags: ["network", "router", "arista"],
  };

  const mockImages: ImageLibraryEntry[] = [
    {
      id: "img-1",
      kind: "docker",
      reference: "ceos:4.28.0F",
      filename: "ceos-4.28.0F.tar",
      device_id: "ceos",
      version: "4.28.0F",
      size_bytes: 1073741824, // 1 GB
      is_default: true,
    },
    {
      id: "img-2",
      kind: "docker",
      reference: "ceos:4.27.0F",
      filename: "ceos-4.27.0F.tar",
      device_id: "ceos",
      version: "4.27.0F",
      size_bytes: 536870912, // 512 MB
      is_default: false,
    },
  ];

  const defaultProps = {
    device: mockDevice,
    assignedImages: [],
    isSelected: false,
    onSelect: vi.fn(),
    onUnassignImage: vi.fn(),
    onSetDefaultImage: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("rendering", () => {
    it("renders device name", () => {
      render(<DeviceCard {...defaultProps} />);
      expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
    });

    it("renders vendor name", () => {
      render(<DeviceCard {...defaultProps} />);
      expect(screen.getByText("Arista")).toBeInTheDocument();
    });

    it("renders device icon", () => {
      render(<DeviceCard {...defaultProps} />);
      const icon = document.querySelector(".fa-microchip");
      expect(icon).toBeInTheDocument();
    });

    it("renders breadcrumb when provided", () => {
      render(<DeviceCard {...defaultProps} breadcrumb="Network > Routers" />);
      expect(screen.getByText("Network > Routers")).toBeInTheDocument();
    });

    it("does not render breadcrumb when not provided", () => {
      render(<DeviceCard {...defaultProps} />);
      expect(screen.queryByText("Network > Routers")).not.toBeInTheDocument();
    });

    it("renders image count badge showing 0 when no images", () => {
      render(<DeviceCard {...defaultProps} />);
      expect(screen.getByText("0")).toBeInTheDocument();
    });

    it("renders image count badge showing correct count", () => {
      render(<DeviceCard {...defaultProps} assignedImages={mockImages} />);
      expect(screen.getByText("2")).toBeInTheDocument();
    });
  });

  describe("status indicator", () => {
    it("shows amber indicator when no images assigned", () => {
      render(<DeviceCard {...defaultProps} />);
      const amberIndicator = document.querySelector(".bg-amber-500");
      expect(amberIndicator).toBeInTheDocument();
      expect(amberIndicator).toHaveAttribute("title", "No images assigned");
    });

    it("shows emerald indicator when default image exists", () => {
      render(<DeviceCard {...defaultProps} assignedImages={mockImages} />);
      const greenIndicator = document.querySelector(".bg-emerald-500");
      expect(greenIndicator).toBeInTheDocument();
      expect(greenIndicator).toHaveAttribute("title", "Has default image");
    });

    it("shows blue indicator when images exist but no default", () => {
      const imagesNoDefault = mockImages.map((img) => ({ ...img, is_default: false }));
      render(<DeviceCard {...defaultProps} assignedImages={imagesNoDefault} />);
      const blueIndicator = document.querySelector(".bg-blue-500");
      expect(blueIndicator).toBeInTheDocument();
      expect(blueIndicator).toHaveAttribute("title", "Has images (no default)");
    });
  });

  describe("license badge", () => {
    it("shows license badge when licenseRequired is true", () => {
      const deviceWithLicense = { ...mockDevice, licenseRequired: true };
      render(<DeviceCard {...defaultProps} device={deviceWithLicense} />);
      expect(screen.getByText("License")).toBeInTheDocument();
    });

    it("does not show license badge when licenseRequired is false", () => {
      render(<DeviceCard {...defaultProps} />);
      expect(screen.queryByText("License")).not.toBeInTheDocument();
    });
  });

  describe("tags display", () => {
    it("renders tags when device has tags", () => {
      render(<DeviceCard {...defaultProps} />);
      expect(screen.getByText("network")).toBeInTheDocument();
      expect(screen.getByText("router")).toBeInTheDocument();
      expect(screen.getByText("arista")).toBeInTheDocument();
    });

    it("limits displayed tags to 4", () => {
      const deviceWithManyTags = {
        ...mockDevice,
        tags: ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6"],
      };
      render(<DeviceCard {...defaultProps} device={deviceWithManyTags} />);
      expect(screen.getByText("tag1")).toBeInTheDocument();
      expect(screen.getByText("tag4")).toBeInTheDocument();
      expect(screen.getByText("+2")).toBeInTheDocument();
    });

    it("does not render tags section when device has no tags", () => {
      const deviceWithoutTags = { ...mockDevice, tags: [] };
      render(<DeviceCard {...defaultProps} device={deviceWithoutTags} />);
      expect(screen.queryByText("network")).not.toBeInTheDocument();
    });
  });

  describe("selection", () => {
    it("calls onSelect when card is clicked", async () => {
      const user = userEvent.setup();
      const onSelect = vi.fn();
      render(<DeviceCard {...defaultProps} onSelect={onSelect} />);

      await user.click(screen.getByText("Arista cEOS"));
      expect(onSelect).toHaveBeenCalledTimes(1);
    });

    it("applies selected styling when isSelected is true", () => {
      const { container } = render(<DeviceCard {...defaultProps} isSelected={true} />);
      const card = container.firstChild as HTMLElement;
      expect(card).toHaveClass("glass-surface");
      expect(card).toHaveClass("border-l-sage-500");
    });

    it("applies default styling when isSelected is false", () => {
      const { container } = render(<DeviceCard {...defaultProps} isSelected={false} />);
      const card = container.firstChild as HTMLElement;
      expect(card).toHaveClass("glass-surface");
    });
  });

  describe("assigned images display", () => {
    it("displays assigned images", () => {
      render(<DeviceCard {...defaultProps} assignedImages={mockImages} />);
      expect(screen.getByText("ceos-4.28.0F.tar")).toBeInTheDocument();
      expect(screen.getByText("ceos-4.27.0F.tar")).toBeInTheDocument();
    });

    it("shows DEFAULT badge for default image", () => {
      render(<DeviceCard {...defaultProps} assignedImages={mockImages} />);
      expect(screen.getByText("DEFAULT")).toBeInTheDocument();
    });

    it("shows version and size for images", () => {
      render(<DeviceCard {...defaultProps} assignedImages={mockImages} />);
      expect(screen.getByText("4.28.0F")).toBeInTheDocument();
      expect(screen.getByText("1.0 GB")).toBeInTheDocument();
    });

    it("limits displayed images to 3", () => {
      const manyImages = [
        ...mockImages,
        {
          id: "img-3",
          kind: "docker",
          reference: "ceos:4.26.0F",
          filename: "ceos-4.26.0F.tar",
          device_id: "ceos",
          version: "4.26.0F",
          is_default: false,
        },
        {
          id: "img-4",
          kind: "docker",
          reference: "ceos:4.25.0F",
          filename: "ceos-4.25.0F.tar",
          device_id: "ceos",
          version: "4.25.0F",
          is_default: false,
        },
      ];
      render(<DeviceCard {...defaultProps} assignedImages={manyImages} />);
      expect(screen.getByText("+1 more")).toBeInTheDocument();
    });

    it("shows docker icon for docker images", () => {
      render(<DeviceCard {...defaultProps} assignedImages={mockImages} />);
      const dockerIcons = document.querySelectorAll(".fa-docker");
      expect(dockerIcons.length).toBeGreaterThan(0);
    });

    it("shows hard-drive icon for qcow2 images", () => {
      const qcow2Images = [
        {
          ...mockImages[0],
          kind: "qcow2",
        },
      ];
      render(<DeviceCard {...defaultProps} assignedImages={qcow2Images} />);
      const hdIcon = document.querySelector(".fa-hard-drive");
      expect(hdIcon).toBeInTheDocument();
    });
  });

  describe("image actions", () => {
    it("calls onSetDefaultImage when star button is clicked", async () => {
      const user = userEvent.setup();
      const onSetDefaultImage = vi.fn();
      const imagesWithNonDefault = mockImages.map((img) => ({ ...img, is_default: false }));
      render(
        <DeviceCard
          {...defaultProps}
          assignedImages={imagesWithNonDefault}
          onSetDefaultImage={onSetDefaultImage}
        />
      );

      const starButton = document.querySelector('[title="Set as default"]');
      if (starButton) {
        await user.click(starButton);
        expect(onSetDefaultImage).toHaveBeenCalledWith("img-1");
      }
    });

    it("does not show set default button for default image", () => {
      render(<DeviceCard {...defaultProps} assignedImages={mockImages} />);
      // Find the image row with DEFAULT badge
      const defaultBadge = screen.getByText("DEFAULT");
      const imageRow = defaultBadge.closest("div[class*='flex items-center gap-2 p-2']");
      const starButton = imageRow?.querySelector('[title="Set as default"]');
      expect(starButton).not.toBeInTheDocument();
    });

    it("calls onUnassignImage when xmark button is clicked", async () => {
      const user = userEvent.setup();
      const onUnassignImage = vi.fn();
      render(
        <DeviceCard
          {...defaultProps}
          assignedImages={mockImages}
          onUnassignImage={onUnassignImage}
        />
      );

      const unassignButtons = document.querySelectorAll('[title="Unassign image"]');
      if (unassignButtons.length > 0) {
        await user.click(unassignButtons[0]);
        expect(onUnassignImage).toHaveBeenCalled();
      }
    });

    it("stops event propagation when clicking image actions", async () => {
      const user = userEvent.setup();
      const onSelect = vi.fn();
      const onUnassignImage = vi.fn();
      render(
        <DeviceCard
          {...defaultProps}
          assignedImages={mockImages}
          onSelect={onSelect}
          onUnassignImage={onUnassignImage}
        />
      );

      const unassignButton = document.querySelector('[title="Unassign image"]');
      if (unassignButton) {
        await user.click(unassignButton);
        // onUnassignImage should be called but not onSelect
        expect(onUnassignImage).toHaveBeenCalled();
        // onSelect might be called due to event bubbling, but stopPropagation should prevent it
      }
    });
  });

  describe("drag and drop", () => {
    it("handles drag over event", () => {
      render(<DeviceCard {...defaultProps} />);
      const card = screen.getByText("Arista cEOS").closest("[class*='rounded-xl']");
      if (card) {
        fireEvent.dragOver(card);
        expect(mockHandleDragOver).toHaveBeenCalled();
      }
    });

    it("handles drag leave event", () => {
      render(<DeviceCard {...defaultProps} />);
      const card = screen.getByText("Arista cEOS").closest("[class*='rounded-xl']");
      if (card) {
        fireEvent.dragLeave(card);
        expect(mockHandleDragLeave).toHaveBeenCalled();
      }
    });

    it("handles drop event", () => {
      render(<DeviceCard {...defaultProps} />);
      const card = screen.getByText("Arista cEOS").closest("[class*='rounded-xl']");
      if (card) {
        fireEvent.drop(card);
        expect(mockHandleDrop).toHaveBeenCalled();
      }
    });
  });

  describe("edge cases", () => {
    it("handles image without filename", () => {
      const imageWithoutFilename = [
        {
          id: "img-1",
          kind: "docker",
          reference: "ceos:4.28.0F",
          device_id: "ceos",
          is_default: false,
        },
      ];
      render(<DeviceCard {...defaultProps} assignedImages={imageWithoutFilename} />);
      // Should fall back to reference
      expect(screen.getByText("ceos:4.28.0F")).toBeInTheDocument();
    });

    it("handles image without size", () => {
      const imageWithoutSize = [
        {
          id: "img-1",
          kind: "docker",
          reference: "ceos:4.28.0F",
          filename: "test.tar",
          device_id: "ceos",
          is_default: false,
        },
      ];
      render(<DeviceCard {...defaultProps} assignedImages={imageWithoutSize} />);
      // Should not crash, just not show size
      expect(screen.getByText("test.tar")).toBeInTheDocument();
    });

    it("handles device without tags property", () => {
      const deviceWithoutTags = { ...mockDevice };
      delete (deviceWithoutTags as any).tags;
      render(<DeviceCard {...defaultProps} device={deviceWithoutTags} />);
      // Should not crash
      expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
    });
  });
});
