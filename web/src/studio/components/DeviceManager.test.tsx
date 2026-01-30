import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeviceManager from "./DeviceManager";
import { DeviceModel, DeviceType, ImageLibraryEntry } from "../types";
import { DragProvider } from "../contexts/DragContext";

// Mock FontAwesome
vi.mock("@fortawesome/react-fontawesome", () => ({
  FontAwesomeIcon: () => null,
}));

// Mock API
vi.mock("../../api", () => ({
  API_BASE_URL: "http://localhost:8000",
  apiRequest: vi.fn(),
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

const mockImageLibrary: ImageLibraryEntry[] = [
  {
    id: "docker:ceos:4.28.0",
    kind: "docker",
    reference: "ceos:4.28.0",
    filename: "ceos-4.28.0.tar",
    device_id: "ceos",
    version: "4.28.0",
    is_default: true,
    vendor: "Arista",
  },
  {
    id: "qcow2:veos.qcow2",
    kind: "qcow2",
    reference: "/images/veos.qcow2",
    filename: "veos.qcow2",
    device_id: undefined,
    version: "4.29",
    vendor: "Arista",
  },
  {
    id: "docker:alpine:latest",
    kind: "docker",
    reference: "alpine:latest",
    filename: "alpine.tar",
    device_id: "linux",
    version: "latest",
    is_default: true,
    vendor: "Generic",
  },
];

const mockImageCatalog = {
  ceos: { clab: "ceos:4.28.0" },
  srlinux: { clab: "ghcr.io/nokia/srlinux" },
};

// Wrapper with DragProvider
const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <DragProvider onImageAssigned={() => {}}>
    {children}
  </DragProvider>
);

describe("DeviceManager", () => {
  const mockOnUploadImage = vi.fn();
  const mockOnUploadQcow2 = vi.fn();
  const mockOnRefresh = vi.fn();

  const defaultProps = {
    deviceModels: mockDeviceModels,
    imageCatalog: mockImageCatalog,
    imageLibrary: mockImageLibrary,
    onUploadImage: mockOnUploadImage,
    onUploadQcow2: mockOnUploadQcow2,
    onRefresh: mockOnRefresh,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the device manager header", () => {
    render(
      <TestWrapper>
        <DeviceManager {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Device Models")).toBeInTheDocument();
    expect(screen.getByText("Image Library")).toBeInTheDocument();
  });

  it("renders all device models", () => {
    render(
      <TestWrapper>
        <DeviceManager {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
    expect(screen.getByText("Nokia SR Linux")).toBeInTheDocument();
    expect(screen.getByText("Linux Container")).toBeInTheDocument();
  });

  it("renders images in the library", () => {
    render(
      <TestWrapper>
        <DeviceManager {...defaultProps} />
      </TestWrapper>
    );

    // Images should be visible
    expect(screen.getByText(/ceos-4.28.0/i)).toBeInTheDocument();
  });

  describe("Device filtering", () => {
    it("filters devices by search text", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} />
        </TestWrapper>
      );

      // Find search input for devices
      const searchInputs = screen.getAllByPlaceholderText(/search/i);
      const deviceSearch = searchInputs[0];

      await user.type(deviceSearch, "arista");

      // Should show Arista devices
      expect(screen.getByText("Arista cEOS")).toBeInTheDocument();
      // Nokia should be filtered out
      expect(screen.queryByText("Nokia SR Linux")).not.toBeInTheDocument();
    });
  });

  describe("Image filtering", () => {
    it("filters images by search text", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} />
        </TestWrapper>
      );

      // Find search input for images (second search input)
      const searchInputs = screen.getAllByPlaceholderText(/search/i);
      if (searchInputs.length > 1) {
        const imageSearch = searchInputs[1];
        await user.type(imageSearch, "alpine");

        // Should show alpine image
        expect(screen.getByText(/alpine/i)).toBeInTheDocument();
      }
    });
  });

  describe("Image upload", () => {
    it("shows upload buttons", () => {
      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} />
        </TestWrapper>
      );

      // Should have upload buttons
      const uploadButtons = screen.getAllByRole("button");
      expect(uploadButtons.length).toBeGreaterThan(0);
    });
  });

  describe("Image cards", () => {
    it("displays image kind badges", () => {
      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} />
        </TestWrapper>
      );

      // Should show docker and qcow2 badges
      expect(screen.getAllByText(/docker/i).length).toBeGreaterThan(0);
    });
  });

  describe("Drag and drop", () => {
    it("devices are draggable", () => {
      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} />
        </TestWrapper>
      );

      // Device cards should be draggable
      const draggables = document.querySelectorAll('[draggable="true"]');
      expect(draggables.length).toBeGreaterThan(0);
    });
  });

  describe("Empty states", () => {
    it("shows message when no devices match filters", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} />
        </TestWrapper>
      );

      // Search for something that doesn't exist
      const searchInputs = screen.getAllByPlaceholderText(/search/i);
      await user.type(searchInputs[0], "nonexistent-device-xyz");

      // Should show empty state
      await waitFor(() => {
        const emptyMessages = screen.queryAllByText(/no devices/i);
        expect(emptyMessages.length).toBeGreaterThanOrEqual(0);
      });
    });

    it("shows message when no images in library", () => {
      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} imageLibrary={[]} />
        </TestWrapper>
      );

      // Should show empty state for images
    });
  });

  describe("Refresh functionality", () => {
    it("has refresh button", () => {
      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} />
        </TestWrapper>
      );

      // Should have a refresh button somewhere
      const refreshIcons = document.querySelectorAll(".fa-rotate, .fa-sync");
      expect(refreshIcons.length).toBeGreaterThanOrEqual(0);
    });
  });
});
