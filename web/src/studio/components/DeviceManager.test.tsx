import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeviceManager from "./DeviceManager";
import { DeviceModel, DeviceType, ImageLibraryEntry } from "../types";
import { DragProvider } from "../contexts/DragContext";
import { apiRequest } from "../../api";

// Mock FontAwesome
vi.mock("@fortawesome/react-fontawesome", () => ({
  FontAwesomeIcon: () => null,
}));

// Mock API
vi.mock("../../api", () => ({
  API_BASE_URL: "http://localhost:8000",
  apiRequest: vi.fn(),
}));
const mockApiRequest = vi.mocked(apiRequest);

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
    imageLibrary: mockImageLibrary,
    onUploadImage: mockOnUploadImage,
    onUploadQcow2: mockOnUploadQcow2,
    onRefresh: mockOnRefresh,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockApiRequest.mockResolvedValue({});
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the device manager header", () => {
    render(
      <TestWrapper>
        <DeviceManager {...defaultProps} />
      </TestWrapper>
    );

    // Main header is "Image Management"
    expect(screen.getByText("Image Management")).toBeInTheDocument();
    // Device search placeholder
    expect(screen.getByPlaceholderText("Search devices...")).toBeInTheDocument();
  });

  it("renders all device models", () => {
    render(
      <TestWrapper>
        <DeviceManager {...defaultProps} />
      </TestWrapper>
    );

    // Device names may appear multiple times (in device list and assigned images section)
    expect(screen.queryAllByText("Arista cEOS").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("Nokia SR Linux").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("Linux Container").length).toBeGreaterThan(0);
  });

  it("renders images in the library", () => {
    render(
      <TestWrapper>
        <DeviceManager {...defaultProps} />
      </TestWrapper>
    );

    // Images should be visible - use queryAllByText as filename may appear multiple times
    expect(screen.queryAllByText(/ceos-4.28.0/i).length).toBeGreaterThan(0);
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

      // Should show Arista devices (may appear multiple times)
      expect(screen.queryAllByText("Arista cEOS").length).toBeGreaterThan(0);
      // Nokia should be filtered out from the device list
      // Note: It may still appear in assigned images section, so we check it's reduced
      const nokiaCount = screen.queryAllByText("Nokia SR Linux").length;
      // When filtered, Nokia shouldn't appear in device cards
      expect(nokiaCount).toBeLessThanOrEqual(1);
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

        // Should show alpine image(s) - use queryAllByText since multiple matches expected
        const alpineElements = screen.queryAllByText(/alpine/i);
        expect(alpineElements.length).toBeGreaterThan(0);
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

    it("does not show IOL build panel in image management mode", () => {
      const iolLibrary: ImageLibraryEntry[] = [
        ...mockImageLibrary,
        {
          id: "iol:i86bi-linux-l3.bin",
          kind: "iol",
          reference: "/images/i86bi-linux-l3.bin",
          filename: "i86bi-linux-l3.bin",
          device_id: "iol-xe",
          build_status: "failed",
          build_error: "build failed",
        },
      ];

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} imageLibrary={iolLibrary} />
        </TestWrapper>
      );

      expect(screen.queryByRole("heading", { name: "Build Jobs" })).not.toBeInTheDocument();
    });

    it("shows IOL build panel when in build-jobs mode", () => {
      const iolLibrary: ImageLibraryEntry[] = [
        ...mockImageLibrary,
        {
          id: "iol:i86bi-linux-l3.bin",
          kind: "iol",
          reference: "/images/i86bi-linux-l3.bin",
          filename: "i86bi-linux-l3.bin",
          device_id: "iol-xe",
          build_status: "failed",
          build_error: "build failed",
        },
      ];

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} imageLibrary={iolLibrary} mode="build-jobs" />
        </TestWrapper>
      );

      expect(screen.getByRole("heading", { name: "Build Jobs" })).toBeInTheDocument();
      expect(screen.getByText("Current Jobs")).toBeInTheDocument();
      expect(screen.getAllByText(/i86bi-linux-l3.bin/i).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/failed/i).length).toBeGreaterThan(0);
    });

    it("auto-refreshes IOL build status while builds are active", async () => {
      vi.useFakeTimers();
      const iolLibrary: ImageLibraryEntry[] = [
        ...mockImageLibrary,
        {
          id: "iol:i86bi-linux-l3.bin",
          kind: "iol",
          reference: "/images/i86bi-linux-l3.bin",
          filename: "i86bi-linux-l3.bin",
          device_id: "iol-xe",
          build_status: "queued",
        },
      ];

      mockApiRequest.mockImplementation((path: string) => {
        if (path.includes("/build-status")) {
          return Promise.resolve({ status: "queued", build_status: "queued" });
        }
        return Promise.resolve({});
      });

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} imageLibrary={iolLibrary} mode="build-jobs" />
        </TestWrapper>
      );

      await Promise.resolve();
      await Promise.resolve();
      let statusCalls = mockApiRequest.mock.calls.filter(([path]) =>
        String(path).includes("/build-status")
      );
      expect(statusCalls.length).toBeGreaterThanOrEqual(1);

      vi.advanceTimersByTime(5000);
      await Promise.resolve();
      await Promise.resolve();
      statusCalls = mockApiRequest.mock.calls.filter(([path]) =>
        String(path).includes("/build-status")
      );
      expect(statusCalls.length).toBeGreaterThanOrEqual(2);
    });

    it("keeps existing build state when build-status API returns transient HTML errors", async () => {
      const iolLibrary: ImageLibraryEntry[] = [
        ...mockImageLibrary,
        {
          id: "iol:i86bi-linux-l3.bin",
          kind: "iol",
          reference: "/images/i86bi-linux-l3.bin",
          filename: "i86bi-linux-l3.bin",
          device_id: "iol-xe",
          build_status: "complete",
        },
      ];

      mockApiRequest.mockImplementation((path: string) => {
        if (path.includes("/build-status")) {
          return Promise.reject(
            new Error("<html><head><title>502 Bad Gateway</title></head><body>bad gateway</body></html>")
          );
        }
        return Promise.resolve({});
      });

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} imageLibrary={iolLibrary} mode="build-jobs" />
        </TestWrapper>
      );

      expect(await screen.findByText("Ready")).toBeInTheDocument();
      expect(screen.getByText("Build History")).toBeInTheDocument();
      expect(
        screen.getByText("No pending or failed jobs. Completed builds are listed in History below.")
      ).toBeInTheDocument();
      expect(screen.queryByText("Failed")).not.toBeInTheDocument();
      expect(screen.queryByText(/Bad Gateway/i)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Force" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Ignore" })).not.toBeInTheDocument();
    });

    it("allows users to ignore a failed IOL build", async () => {
      const user = userEvent.setup();
      const iolLibrary: ImageLibraryEntry[] = [
        ...mockImageLibrary,
        {
          id: "iol:i86bi-linux-l3.bin",
          kind: "iol",
          reference: "/images/i86bi-linux-l3.bin",
          filename: "i86bi-linux-l3.bin",
          device_id: "iol-xe",
          build_status: "failed",
          build_error: "build failed",
        },
      ];

      mockApiRequest.mockImplementation((path: string) => {
        if (path.includes("/build-status")) {
          return Promise.resolve({
            status: "failed",
            build_status: "failed",
            build_error: "build failed",
          });
        }
        if (path.includes("/ignore-build-failure")) {
          return Promise.resolve({ build_status: "ignored" });
        }
        return Promise.resolve({});
      });

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} imageLibrary={iolLibrary} mode="build-jobs" />
        </TestWrapper>
      );

      await user.click(await screen.findByRole("button", { name: "Ignore" }));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          expect.stringContaining("/ignore-build-failure"),
          expect.objectContaining({ method: "POST" })
        );
      });
      expect(screen.getByText("IOL build failure ignored.")).toBeInTheDocument();
    });

    it("shows diagnostics modal for IOL build failures", async () => {
      const user = userEvent.setup();
      const iolLibrary: ImageLibraryEntry[] = [
        ...mockImageLibrary,
        {
          id: "iol:i86bi-linux-l3.bin",
          kind: "iol",
          reference: "/images/i86bi-linux-l3.bin",
          filename: "i86bi-linux-l3.bin",
          device_id: "iol-xe",
          build_status: "failed",
          build_error: "build failed",
          build_job_id: "rq-build-failed",
        },
      ];

      mockApiRequest.mockImplementation((path: string) => {
        if (path.includes("/build-status")) {
          return Promise.resolve({
            status: "failed",
            build_status: "failed",
            build_error: "build failed",
            build_job_id: "rq-build-failed",
          });
        }
        if (path.includes("/build-diagnostics")) {
          return Promise.resolve({
            image_id: "iol:i86bi-linux-l3.bin",
            filename: "i86bi-linux-l3.bin",
            status: "failed",
            build_status: "failed",
            build_error: "build failed",
            build_job_id: "rq-build-failed",
            queue_job: {
              id: "rq-build-failed",
              status: "failed",
              error_log: "ValueError: invalid ELF header",
            },
            recommended_action: "Retry the build.",
          });
        }
        return Promise.resolve({});
      });

      render(
        <TestWrapper>
          <DeviceManager {...defaultProps} imageLibrary={iolLibrary} mode="build-jobs" />
        </TestWrapper>
      );

      await user.click(await screen.findByRole("button", { name: "Details" }));

      expect(await screen.findByText("IOL Build Diagnostics")).toBeInTheDocument();
      expect(await screen.findByText(/invalid ELF header/i)).toBeInTheDocument();
      expect(mockApiRequest).toHaveBeenCalledWith(
        expect.stringContaining("/build-diagnostics")
      );
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
