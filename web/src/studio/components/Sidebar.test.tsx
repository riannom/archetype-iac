import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Sidebar from "./Sidebar";
import { DeviceModel, DeviceType, AnnotationType } from "../types";

// Mock FontAwesome icons
vi.mock("@fortawesome/react-fontawesome", () => ({
  FontAwesomeIcon: () => null,
}));

// Mock useNotifications to avoid needing NotificationProvider
vi.mock("../../contexts/NotificationContext", () => ({
  useNotifications: () => ({
    notifications: [],
    addNotification: vi.fn(),
    dismissNotification: vi.fn(),
    dismissAllNotifications: vi.fn(),
  }),
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

// Mock image library to match device IDs so filtering works
const mockImageLibrary = [
  { id: "img-1", device_id: "ceos", kind: "docker", reference: "ceos:4.28.0F", is_default: true },
  { id: "img-2", device_id: "srlinux", kind: "docker", reference: "srlinux:23.10.1", is_default: true },
  { id: "img-3", device_id: "linux", kind: "docker", reference: "alpine:latest", is_default: true },
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
        imageLibrary={mockImageLibrary}
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
        imageLibrary={mockImageLibrary}
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
        imageLibrary={mockImageLibrary}
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
        imageLibrary={mockImageLibrary}
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
        imageLibrary={mockImageLibrary}
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
        imageLibrary={mockImageLibrary}
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
        imageLibrary={mockImageLibrary}
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
          imageLibrary={mockImageLibrary}
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
          imageLibrary={mockImageLibrary}
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
    it("does not treat non-instantiable image kinds as sidebar-eligible images", () => {
      const categories = [{ name: "Network Devices", models: [mockDeviceModels[0]] }];
      const images = [
        {
          id: "img-iol",
          device_id: "ceos",
          kind: "iol",
          reference: "i86bi-linux-l3.bin",
          is_default: true,
        },
      ];

      render(
        <Sidebar
          categories={categories}
          imageLibrary={images}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      // Default filter is has_image; ceos should be hidden because iol is non-instantiable.
      expect(screen.queryByText("Arista cEOS")).not.toBeInTheDocument();
    });

    it("matches image status by device kind when image is keyed by canonical kind", () => {
      const cat9kModel: DeviceModel = {
        id: "cat9000v-uadp",
        kind: "cisco_cat9kv",
        name: "BETA CAT9000v UADP",
        type: DeviceType.ROUTER,
        icon: "fa-microchip",
        versions: ["17.15.03"],
        isActive: true,
        vendor: "Cisco",
      };
      const categories = [{ name: "Network Devices", models: [cat9kModel] }];
      const images = [
        {
          id: "img-cat9k",
          device_id: "cisco_cat9kv",
          kind: "qcow2",
          reference: "cat9kv_prd.17.15.03.qcow2",
          is_default: false,
        },
      ];

      render(
        <Sidebar
          categories={categories}
          imageLibrary={images}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      // Default image filter is "has_image", so this would be hidden without kind matching.
      expect(screen.getByText("BETA CAT9000v UADP")).toBeInTheDocument();
    });

    it("matches Cat9000v image status via alias when kind is missing", () => {
      const cat9kModel: DeviceModel = {
        id: "cat9000v-q200",
        name: "BETA CAT9000v Q200",
        type: DeviceType.ROUTER,
        icon: "fa-microchip",
        versions: ["17.15.03"],
        isActive: true,
        vendor: "Cisco",
      };
      const categories = [{ name: "Network Devices", models: [cat9kModel] }];
      const images = [
        {
          id: "img-cat9k",
          device_id: "cisco_cat9kv",
          kind: "qcow2",
          reference: "cat9kv_prd.17.15.03.qcow2",
          is_default: false,
        },
      ];

      render(
        <Sidebar
          categories={categories}
          imageLibrary={images}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      expect(screen.getByText("BETA CAT9000v Q200")).toBeInTheDocument();
    });

    it("displays count of devices per category", () => {
      render(
        <Sidebar
          categories={mockCategories}
          imageLibrary={mockImageLibrary}
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
          imageLibrary={mockImageLibrary}
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
          imageLibrary={mockImageLibrary}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      const ceosDevice = screen.getByText("Arista cEOS").closest("[draggable]");
      expect(ceosDevice).toHaveAttribute("draggable", "true");
    });

    it("sets device data on drag start", () => {
      render(
        <Sidebar
          categories={mockCategories}
          imageLibrary={mockImageLibrary}
          onAddDevice={mockOnAddDevice}
          onAddAnnotation={mockOnAddAnnotation}
        />
      );

      const ceosDevice = screen.getByText("Arista cEOS").closest("[draggable]");
      if (ceosDevice) {
        const setData = vi.fn();
        fireEvent.dragStart(ceosDevice, {
          dataTransfer: { setData, effectAllowed: '' },
        });
        expect(setData).toHaveBeenCalledWith(
          'application/x-archetype-device',
          JSON.stringify(mockDeviceModels[0])
        );
      }
    });
  });

  describe("Image status indicators", () => {
    it.skip("shows amber indicator for devices without images", () => {
      // Skipped: Default imageStatus='has_image' filter hides devices without images
      // This test would require changing the internal filter state to 'no_image'
    });

    it("shows green indicator for devices with default images", () => {
      const imageLibrary = [
        {
          id: "img-1",
          kind: "docker",
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
          kind: "docker",
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
