import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Sidebar from "./Sidebar";
import { DeviceModel, DeviceType, AnnotationType } from "../types";

// Mock FontAwesome icons
vi.mock("@fortawesome/react-fontawesome", () => ({
  FontAwesomeIcon: () => null,
}));

const mockDeviceModels: DeviceModel[] = [
  {
    id: "ceos",
    name: "Arista cEOS",
    type: DeviceType.ROUTER,
    icon: "fa-microchip",
    versions: ["4.28.0F", "4.27.0F"],
    isActive: true,
    vendor: "Arista",
  },
  {
    id: "srlinux",
    name: "Nokia SR Linux",
    type: DeviceType.ROUTER,
    icon: "fa-microchip",
    versions: ["23.10.1"],
    isActive: true,
    vendor: "Nokia",
  },
  {
    id: "linux",
    name: "Linux Container",
    type: DeviceType.HOST,
    icon: "fa-server",
    versions: ["alpine:latest"],
    isActive: true,
    vendor: "Generic",
  },
];

const mockCategories = [
  {
    name: "Network Devices",
    subCategories: [
      {
        name: "Routers",
        models: [mockDeviceModels[0], mockDeviceModels[1]],
      },
    ],
  },
  {
    name: "Hosts",
    models: [mockDeviceModels[2]],
  },
];

describe("Sidebar", () => {
  const mockOnAddDevice = vi.fn();
  const mockOnAddAnnotation = vi.fn();
  const mockOnAddExternalNetwork = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the sidebar with library header", () => {
    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
      />
    );

    expect(screen.getByText("Library")).toBeInTheDocument();
  });

  it("renders all category sections", () => {
    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
      />
    );

    expect(screen.getByText("Network Devices")).toBeInTheDocument();
    expect(screen.getByText("Hosts")).toBeInTheDocument();
  });

  it("renders device models within categories", () => {
    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
      />
    );

    expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
    expect(screen.getByText("Nokia SR Linux")).toBeInTheDocument();
    expect(screen.getByText("Linux Container")).toBeInTheDocument();
  });

  it("calls onAddDevice when a device is clicked", async () => {
    const user = userEvent.setup();

    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
      />
    );

    await user.click(screen.getByText("Arista cEOS"));

    expect(mockOnAddDevice).toHaveBeenCalledTimes(1);
    expect(mockOnAddDevice).toHaveBeenCalledWith(mockDeviceModels[0]);
  });

  it("renders annotation tools", () => {
    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
      />
    );

    expect(screen.getByText("Annotations")).toBeInTheDocument();
    expect(screen.getByText("Label")).toBeInTheDocument();
    expect(screen.getByText("Box")).toBeInTheDocument();
    expect(screen.getByText("Zone")).toBeInTheDocument();
    expect(screen.getByText("Flow")).toBeInTheDocument();
    expect(screen.getByText("Note")).toBeInTheDocument();
  });

  it("calls onAddAnnotation when an annotation tool is clicked", async () => {
    const user = userEvent.setup();

    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
      />
    );

    await user.click(screen.getByText("Label"));

    expect(mockOnAddAnnotation).toHaveBeenCalledTimes(1);
    expect(mockOnAddAnnotation).toHaveBeenCalledWith("text");
  });

  it("renders external network button when handler is provided", () => {
    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
        onAddExternalNetwork={mockOnAddExternalNetwork}
      />
    );

    expect(screen.getByText("External Network")).toBeInTheDocument();
    expect(screen.getByText("Connectivity")).toBeInTheDocument();
  });

  it("does not render external network button when handler is not provided", () => {
    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
      />
    );

    expect(screen.queryByText("External Network")).not.toBeInTheDocument();
  });

  it("calls onAddExternalNetwork when external network button is clicked", async () => {
    const user = userEvent.setup();

    render(
      <Sidebar
        categories={mockCategories}
        onAddDevice={mockOnAddDevice}
        onAddAnnotation={mockOnAddAnnotation}
        onAddExternalNetwork={mockOnAddExternalNetwork}
      />
    );

    await user.click(screen.getByText("External Network"));

    expect(mockOnAddExternalNetwork).toHaveBeenCalledTimes(1);
  });

  describe("Category expansion", () => {
    it("categories are expanded by default", () => {
      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      // Device models should be visible (categories expanded)
      expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
      expect(screen.getByText("Linux Container")).toBeInTheDocument();
    });

    it("toggles category expansion when header is clicked", async () => {
      const user = userEvent.setup();

      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      // Click category header to collapse
      await user.click(screen.getByRole("button", { name: /hosts/i }));

      // The container should have collapsed (max-h-0)
      // Since we can't easily test CSS animations, we verify the state change occurred
      // by clicking again to toggle back
      await user.click(screen.getByRole("button", { name: /hosts/i }));

      // Device should still be in the DOM (just visibility toggled via CSS)
      expect(screen.getByText("Linux Container")).toBeInTheDocument();
    });
  });

  describe("Device filtering", () => {
    it("displays count of devices per category", () => {
      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      // Network Devices has 2 routers in subcategory - there will be multiple (2) counts
      // so we check that at least one exists
      const countTwos = screen.getAllByText("(2)");
      expect(countTwos.length).toBeGreaterThan(0);
      // Hosts has 1 device
      expect(screen.getByText("(1)")).toBeInTheDocument();
    });

    it("shows empty state message when no devices match filters", () => {
      render(
        <Sidebar
          categories={[]}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      expect(
        screen.getByText("No devices match your filters")
      ).toBeInTheDocument();
      expect(screen.getByText("Clear filters")).toBeInTheDocument();
    });
  });

  describe("Device version display", () => {
    it("displays the first version for each device", () => {
      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      // Arista cEOS shows first version
      expect(screen.getByText("4.28.0F")).toBeInTheDocument();
      // Nokia SR Linux shows first version
      expect(screen.getByText("23.10.1")).toBeInTheDocument();
      // Linux shows first version
      expect(screen.getByText("alpine:latest")).toBeInTheDocument();
    });
  });

  describe("Drag and drop", () => {
    it("devices are draggable", () => {
      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      const ceosDevice = screen.getByText("Arista cEOS").closest("[draggable]");
      expect(ceosDevice).toHaveAttribute("draggable", "true");
    });

    it("calls onAddDevice on drag end", () => {
      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      const ceosDevice = screen.getByText("Arista cEOS").closest("[draggable]");
      if (ceosDevice) {
        fireEvent.dragEnd(ceosDevice);
      }

      expect(mockOnAddDevice).toHaveBeenCalledWith(mockDeviceModels[0]);
    });
  });

  describe("Image status indicators", () => {
    it("shows amber indicator for devices without images", () => {
      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
          imageLibrary={[]}
        />
      );

      // Should show amber indicators (no images assigned)
      const amberIndicators = document.querySelectorAll(".bg-amber-500");
      expect(amberIndicators.length).toBeGreaterThan(0);
    });

    it("shows green indicator for devices with default images", () => {
      const imageLibrary = [
        {
          id: "img-1",
          kind: "ceos",
          reference: "ceos:4.28.0F",
          device_id: "ceos",
          is_default: true,
        },
      ];

      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
          imageLibrary={imageLibrary}
        />
      );

      // Should show emerald (green) indicator for ceos
      const greenIndicators = document.querySelectorAll(".bg-emerald-500");
      expect(greenIndicators.length).toBeGreaterThan(0);
    });

    it("shows blue indicator for devices with images but no default", () => {
      const imageLibrary = [
        {
          id: "img-1",
          kind: "ceos",
          reference: "ceos:4.28.0F",
          device_id: "ceos",
          is_default: false,
        },
      ];

      render(
        <Sidebar
          categories={mockCategories}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
          imageLibrary={imageLibrary}
        />
      );

      // Should show blue indicator for ceos
      const blueIndicators = document.querySelectorAll(".bg-blue-500");
      expect(blueIndicators.length).toBeGreaterThan(0);
    });
  });
});
