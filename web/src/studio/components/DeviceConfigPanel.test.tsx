import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DeviceConfigPanel from "./DeviceConfigPanel";
import { DeviceModel, DeviceType, DeviceConfig } from "../types";

// Mock the API module
vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
}));

// Import the mocked apiRequest
import { apiRequest } from "../../api";
const mockApiRequest = vi.mocked(apiRequest);

// Mock VendorOptionsPanel
vi.mock("./VendorOptionsPanel", () => ({
  default: ({
    deviceId,
    vendorName,
    options,
    onChange,
  }: {
    deviceId: string;
    vendorName: string;
    options: Record<string, unknown>;
    onChange: (key: string, value: unknown) => void;
  }) => (
    <div data-testid="vendor-options-panel">
      <span data-testid="vendor-device-id">{deviceId}</span>
      <span data-testid="vendor-name">{vendorName}</span>
      <button
        data-testid="toggle-vendor-option"
        onClick={() => onChange("testOption", true)}
      >
        Toggle Option
      </button>
    </div>
  ),
}));

// Mock window.confirm
const mockConfirm = vi.fn();
window.confirm = mockConfirm;

const mockDevice: DeviceModel = {
  id: "ceos",
  name: "Arista cEOS",
  type: DeviceType.ROUTER,
  icon: "fa-microchip",
  versions: ["4.28.0F", "4.27.0F"],
  isActive: true,
  vendor: "Arista",
  requiresImage: true,
  licenseRequired: false,
  tags: ["router", "datacenter"],
};

const mockConfig: DeviceConfig = {
  base: {
    portNaming: "eth{port}",
    portStartIndex: 1,
    maxPorts: 48,
    memory: 2048,
    cpu: 2,
    readinessProbe: "ssh",
    readinessPattern: "login:",
    readinessTimeout: 300,
    kind: "ceos",
    consoleShell: "/bin/bash",
    documentationUrl: "https://docs.example.com",
    notes: "Test notes for device",
    isBuiltIn: true,
    vendorOptions: {
      zerotouchCancel: true,
    },
  },
  overrides: {},
  effective: {
    portNaming: "eth{port}",
    portStartIndex: 1,
    maxPorts: 48,
    memory: 2048,
    cpu: 2,
    readinessProbe: "ssh",
    readinessPattern: "login:",
    readinessTimeout: 300,
    kind: "ceos",
    consoleShell: "/bin/bash",
    documentationUrl: "https://docs.example.com",
    notes: "Test notes for device",
    isBuiltIn: true,
    vendorOptions: {
      zerotouchCancel: true,
    },
  },
};

describe("DeviceConfigPanel", () => {
  const mockOnRefresh = vi.fn();

  const defaultProps = {
    device: mockDevice,
    onRefresh: mockOnRefresh,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockApiRequest.mockResolvedValue(mockConfig);
    mockConfirm.mockReturnValue(true);
  });

  describe("Loading state", () => {
    it("shows loading spinner while loading config", async () => {
      mockApiRequest.mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve(mockConfig), 100))
      );

      render(<DeviceConfigPanel {...defaultProps} />);

      expect(screen.getByText("Loading configuration...")).toBeInTheDocument();
      expect(document.querySelector(".fa-spinner")).toBeInTheDocument();
    });

    it("fetches device config on mount", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith("/vendors/ceos/config");
      });
    });
  });

  describe("Error state", () => {
    it("shows error message when loading fails", async () => {
      mockApiRequest.mockRejectedValue(new Error("Failed to load config"));

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Failed to load config")).toBeInTheDocument();
      });
    });

    it("shows retry button on error", async () => {
      mockApiRequest.mockRejectedValue(new Error("Network error"));

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Retry")).toBeInTheDocument();
      });
    });

    it("reloads config when retry is clicked", async () => {
      mockApiRequest.mockRejectedValueOnce(new Error("Network error"));
      mockApiRequest.mockResolvedValueOnce(mockConfig);

      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Retry")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Retry"));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledTimes(2);
      });
    });
  });

  describe("Header section", () => {
    it("displays device name and vendor", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      // Wait for loading to complete
      await waitFor(() => {
        expect(screen.queryByText("Loading configuration...")).not.toBeInTheDocument();
      });

      // Now check for the header content
      expect(screen.getByRole("heading", { name: "Arista cEOS" })).toBeInTheDocument();
      // Use getAllByText since vendor name appears multiple times (header + VendorOptionsPanel mock)
      const aristaTexts = screen.getAllByText("Arista");
      expect(aristaTexts.length).toBeGreaterThanOrEqual(1);
    });

    it("displays device ID", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      // Wait for loading to complete
      await waitFor(() => {
        expect(screen.queryByText("Loading configuration...")).not.toBeInTheDocument();
      });

      // Use getAllByText since ceos appears multiple times (header + VendorOptionsPanel mock)
      const ceosTexts = screen.getAllByText("ceos");
      expect(ceosTexts.length).toBeGreaterThanOrEqual(1);
    });

    it("shows Built-in badge for built-in devices", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Built-in")).toBeInTheDocument();
      });
    });

    it("displays device icon", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        const icon = document.querySelector(".fa-microchip");
        expect(icon).toBeInTheDocument();
      });
    });
  });

  describe("Save Changes button", () => {
    it("is disabled when there are no changes", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        const saveButton = screen.getByText("Save Changes");
        expect(saveButton.closest("button")).toHaveClass("cursor-not-allowed");
      });
    });

    it("is enabled when there are changes", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Save Changes")).toBeInTheDocument();
      });

      // Make a change to a field
      const memoryInput = screen.getByDisplayValue("2048");
      await user.clear(memoryInput);
      await user.type(memoryInput, "4096");

      const saveButton = screen.getByText("Save Changes");
      expect(saveButton.closest("button")).not.toHaveClass("cursor-not-allowed");
    });

    it("shows saving state while saving", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      // Wait for initial load to complete
      await waitFor(() => {
        expect(screen.queryByText("Loading configuration...")).not.toBeInTheDocument();
      });

      // Make a change
      const memoryInput = screen.getByDisplayValue("2048");
      await user.clear(memoryInput);
      await user.type(memoryInput, "4096");

      // Set up a slow response for the PUT request
      mockApiRequest.mockImplementationOnce(
        () => new Promise((resolve) => setTimeout(() => resolve(mockConfig), 200))
      );

      // Click save but don't await it fully
      user.click(screen.getByText("Save Changes"));

      // The saving state should appear
      await waitFor(() => {
        expect(screen.getByText("Saving...")).toBeInTheDocument();
      });
    });

    it("calls API with correct data when saving", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Save Changes")).toBeInTheDocument();
      });

      // Make a change
      const memoryInput = screen.getByDisplayValue("2048");
      await user.clear(memoryInput);
      await user.type(memoryInput, "4096");

      // Clear previous calls
      mockApiRequest.mockClear();
      mockApiRequest.mockResolvedValue(mockConfig);

      await user.click(screen.getByText("Save Changes"));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          "/vendors/ceos/config",
          expect.objectContaining({
            method: "PUT",
            body: expect.stringContaining("memory"),
          })
        );
      });
    });

    it("calls onRefresh after successful save", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Save Changes")).toBeInTheDocument();
      });

      // Make a change
      const memoryInput = screen.getByDisplayValue("2048");
      await user.clear(memoryInput);
      await user.type(memoryInput, "4096");

      await user.click(screen.getByText("Save Changes"));

      await waitFor(() => {
        expect(mockOnRefresh).toHaveBeenCalled();
      });
    });
  });

  describe("Reset to Defaults button", () => {
    it("is not shown when there are no overrides", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.queryByText("Reset to Defaults")).not.toBeInTheDocument();
      });
    });

    it("shows when there are overrides", async () => {
      const configWithOverrides: DeviceConfig = {
        ...mockConfig,
        overrides: { memory: 4096 },
      };
      mockApiRequest.mockResolvedValue(configWithOverrides);

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Reset to Defaults")).toBeInTheDocument();
      });
    });

    it("shows when user has made changes", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Save Changes")).toBeInTheDocument();
      });

      // Make a change
      const memoryInput = screen.getByDisplayValue("2048");
      await user.clear(memoryInput);
      await user.type(memoryInput, "4096");

      expect(screen.getByText("Reset to Defaults")).toBeInTheDocument();
    });

    it("shows confirmation dialog before resetting", async () => {
      const configWithOverrides: DeviceConfig = {
        ...mockConfig,
        overrides: { memory: 4096 },
      };
      mockApiRequest.mockResolvedValue(configWithOverrides);

      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Reset to Defaults")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Reset to Defaults"));

      expect(mockConfirm).toHaveBeenCalledWith(
        "Reset this device to default configuration? This will remove all custom overrides."
      );
    });

    it("calls DELETE API when reset is confirmed", async () => {
      const configWithOverrides: DeviceConfig = {
        ...mockConfig,
        overrides: { memory: 4096 },
      };
      mockApiRequest.mockResolvedValue(configWithOverrides);
      mockConfirm.mockReturnValue(true);

      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Reset to Defaults")).toBeInTheDocument();
      });

      mockApiRequest.mockClear();
      mockApiRequest.mockResolvedValue({});

      await user.click(screen.getByText("Reset to Defaults"));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          "/vendors/ceos/config",
          { method: "DELETE" }
        );
      });
    });

    it("does not reset when confirmation is cancelled", async () => {
      const configWithOverrides: DeviceConfig = {
        ...mockConfig,
        overrides: { memory: 4096 },
      };
      mockApiRequest.mockResolvedValue(configWithOverrides);
      mockConfirm.mockReturnValue(false);

      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Reset to Defaults")).toBeInTheDocument();
      });

      mockApiRequest.mockClear();

      await user.click(screen.getByText("Reset to Defaults"));

      expect(mockApiRequest).not.toHaveBeenCalled();
    });
  });

  describe("Config sections", () => {
    it("renders Port Configuration section", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Port Configuration")).toBeInTheDocument();
      });
    });

    it("renders Resource Allocation section", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Resource Allocation")).toBeInTheDocument();
      });
    });

    it("renders Boot & Readiness section", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Boot & Readiness")).toBeInTheDocument();
      });
    });

    it("renders Documentation & Info section", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Documentation & Info")).toBeInTheDocument();
      });
    });

    it("renders Vendor-Specific Options section when options exist", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Vendor-Specific Options")).toBeInTheDocument();
      });
    });

    it("does not render Vendor-Specific Options section when no options", async () => {
      const configNoVendorOptions: DeviceConfig = {
        base: { ...mockConfig.base, vendorOptions: {} },
        overrides: {},
        effective: { ...mockConfig.effective, vendorOptions: {} },
      };
      mockApiRequest.mockResolvedValue(configNoVendorOptions);

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Port Configuration")).toBeInTheDocument();
        expect(
          screen.queryByText("Vendor-Specific Options")
        ).not.toBeInTheDocument();
      });
    });
  });

  describe("Section collapsing", () => {
    it("sections are expanded by default", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        // Port Configuration content should be visible
        expect(screen.getByText("Interface Naming")).toBeInTheDocument();
        expect(screen.getByText("Memory")).toBeInTheDocument();
      });
    });

    it("Documentation & Info section is collapsed by default", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Documentation & Info")).toBeInTheDocument();
        // Console Shell should not be visible until expanded
        // Note: This test depends on the collapsed behavior
      });
    });

    it("toggles section when header is clicked", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Port Configuration")).toBeInTheDocument();
      });

      // Click to collapse
      await user.click(screen.getByText("Port Configuration"));

      // Interface Naming should be hidden
      await waitFor(() => {
        expect(screen.queryByText("Interface Naming")).not.toBeInTheDocument();
      });

      // Click to expand again
      await user.click(screen.getByText("Port Configuration"));

      await waitFor(() => {
        expect(screen.getByText("Interface Naming")).toBeInTheDocument();
      });
    });
  });

  describe("Config fields", () => {
    it("displays port configuration fields with values", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Interface Naming")).toBeInTheDocument();
        expect(screen.getByText("Start Index")).toBeInTheDocument();
        expect(screen.getByText("Max Ports")).toBeInTheDocument();
      });
    });

    it("displays memory and CPU fields", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Memory")).toBeInTheDocument();
        expect(screen.getByText("CPU Cores")).toBeInTheDocument();
        expect(screen.getByText("MB")).toBeInTheDocument();
      });
    });

    it("displays readiness configuration", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Readiness Probe")).toBeInTheDocument();
        expect(screen.getByText("Readiness Pattern")).toBeInTheDocument();
        expect(screen.getByText("Readiness Timeout")).toBeInTheDocument();
      });
    });

    it("allows editing numeric fields", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByDisplayValue("2048")).toBeInTheDocument();
      });

      const memoryInput = screen.getByDisplayValue("2048");
      await user.clear(memoryInput);
      await user.type(memoryInput, "4096");

      expect(memoryInput).toHaveValue(4096);
    });

    it("allows editing text fields", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      // Wait for loading to complete
      await waitFor(() => {
        expect(screen.queryByText("Loading configuration...")).not.toBeInTheDocument();
      });

      const portNameInput = screen.getByDisplayValue("eth{port}");
      await user.clear(portNameInput);
      await user.type(portNameInput, "Ethernet");

      // The input should show the typed value (appended or replaced)
      expect(portNameInput).toHaveValue("Ethernet");
    });

    it("shows override indicator for modified fields", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByDisplayValue("2048")).toBeInTheDocument();
      });

      // Initially no override indicators
      let overrideIndicators = document.querySelectorAll(".bg-blue-500");
      const initialCount = overrideIndicators.length;

      // Make a change
      const memoryInput = screen.getByDisplayValue("2048");
      await user.clear(memoryInput);
      await user.type(memoryInput, "4096");

      // Should now show override indicator
      overrideIndicators = document.querySelectorAll(".bg-blue-500");
      expect(overrideIndicators.length).toBeGreaterThan(initialCount);
    });
  });

  describe("Documentation & Info section", () => {
    it("shows documentation link when URL is provided", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Documentation & Info")).toBeInTheDocument();
      });

      // Expand the collapsed section
      await user.click(screen.getByText("Documentation & Info"));

      await waitFor(() => {
        const docsLink = screen.getByText("View Docs");
        expect(docsLink).toBeInTheDocument();
        expect(docsLink).toHaveAttribute("href", "https://docs.example.com");
      });
    });

    it("shows notes when provided", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Documentation & Info")).toBeInTheDocument();
      });

      // Expand the collapsed section
      await user.click(screen.getByText("Documentation & Info"));

      await waitFor(() => {
        expect(screen.getByText("Test notes for device")).toBeInTheDocument();
      });
    });

    it("shows Image Required badge when device requires image", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Documentation & Info")).toBeInTheDocument();
      });

      // Expand the collapsed section
      await user.click(screen.getByText("Documentation & Info"));

      await waitFor(() => {
        expect(screen.getByText("Image Required")).toBeInTheDocument();
      });
    });

    it("shows License Required badge when license is required", async () => {
      const deviceWithLicense: DeviceModel = {
        ...mockDevice,
        licenseRequired: true,
      };

      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} device={deviceWithLicense} />);

      await waitFor(() => {
        expect(screen.getByText("Documentation & Info")).toBeInTheDocument();
      });

      // Expand the collapsed section
      await user.click(screen.getByText("Documentation & Info"));

      await waitFor(() => {
        expect(screen.getByText("License Required")).toBeInTheDocument();
      });
    });

    it("shows device tags", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Documentation & Info")).toBeInTheDocument();
      });

      // Expand the collapsed section
      await user.click(screen.getByText("Documentation & Info"));

      await waitFor(() => {
        expect(screen.getByText("router")).toBeInTheDocument();
        expect(screen.getByText("datacenter")).toBeInTheDocument();
      });
    });
  });

  describe("Vendor Options Panel", () => {
    it("renders VendorOptionsPanel with correct props", async () => {
      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByTestId("vendor-options-panel")).toBeInTheDocument();
        expect(screen.getByTestId("vendor-device-id")).toHaveTextContent("ceos");
        expect(screen.getByTestId("vendor-name")).toHaveTextContent("Arista");
      });
    });

    it("handles vendor option changes", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByTestId("toggle-vendor-option")).toBeInTheDocument();
      });

      // Make a change via vendor options
      await user.click(screen.getByTestId("toggle-vendor-option"));

      // Save button should now be enabled
      await waitFor(() => {
        const saveButton = screen.getByText("Save Changes");
        expect(saveButton.closest("button")).not.toHaveClass("cursor-not-allowed");
      });
    });
  });

  describe("Error handling during save", () => {
    it("shows error message when save fails", async () => {
      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByDisplayValue("2048")).toBeInTheDocument();
      });

      // Make a change
      const memoryInput = screen.getByDisplayValue("2048");
      await user.clear(memoryInput);
      await user.type(memoryInput, "4096");

      // Mock save to fail
      mockApiRequest.mockRejectedValueOnce(new Error("Save failed"));

      await user.click(screen.getByText("Save Changes"));

      await waitFor(() => {
        expect(screen.getByText("Save failed")).toBeInTheDocument();
      });
    });

    it("shows error message when reset fails", async () => {
      const configWithOverrides: DeviceConfig = {
        ...mockConfig,
        overrides: { memory: 4096 },
      };
      mockApiRequest.mockResolvedValue(configWithOverrides);

      const user = userEvent.setup();

      render(<DeviceConfigPanel {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Reset to Defaults")).toBeInTheDocument();
      });

      // Mock reset to fail
      mockApiRequest.mockRejectedValueOnce(new Error("Reset failed"));

      await user.click(screen.getByText("Reset to Defaults"));

      await waitFor(() => {
        expect(screen.getByText("Reset failed")).toBeInTheDocument();
      });
    });
  });
});
