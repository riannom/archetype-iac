import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DeviceModel, DeviceType, ImageLibraryEntry } from "../types";
import type { ImageStatus } from "./SidebarFilters";

let SidebarFilters: typeof import("./SidebarFilters").default;

const mockFilterChip = ({
  label,
  isActive,
  onClick,
  count,
  variant,
  statusColor,
}: {
  label: string;
  isActive: boolean;
  onClick: () => void;
  count?: number;
  variant?: string;
  statusColor?: string;
}) => (
  <button
    data-testid={`filter-chip-${label.toLowerCase().replace(/\s+/g, "-")}`}
    data-active={isActive}
    data-variant={variant}
    data-status-color={statusColor}
    onClick={onClick}
  >
    {label}
    {count !== undefined && <span data-testid={`count-${label}`}>{count}</span>}
  </button>
);

export function registerSidebarFiltersTests() {
  describe("SidebarFilters", () => {
    const mockDevices: DeviceModel[] = [
      {
        id: "ceos",
        name: "Arista cEOS",
        type: DeviceType.ROUTER,
        icon: "fa-microchip",
        versions: ["4.28.0F"],
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
        id: "veos",
        name: "Arista vEOS",
        type: DeviceType.SWITCH,
        icon: "fa-server",
        versions: ["4.27.0F"],
        isActive: true,
        vendor: "Arista",
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
        id: "img-1",
        kind: "qcow2",
        reference: "ceos:4.28.0F",
        device_id: "ceos",
        is_default: true,
      },
      {
        id: "img-2",
        kind: "docker",
        reference: "srlinux:23.10.1",
        device_id: "srlinux",
        is_default: false,
      },
    ];

    const defaultProps = {
      devices: mockDevices,
      imageLibrary: mockImageLibrary,
      searchQuery: "",
      onSearchChange: vi.fn(),
      selectedVendors: new Set<string>(),
      onVendorToggle: vi.fn(),
      selectedTypes: new Set<string>(),
      onTypeToggle: vi.fn(),
      imageStatus: "all" as ImageStatus,
      onImageStatusChange: vi.fn(),
      onClearAll: vi.fn(),
    };

    beforeEach(async () => {
      vi.clearAllMocks();
      vi.resetModules();
      vi.doUnmock("./FilterChip");
      vi.doUnmock("./SidebarFilters");
      const filterChipModule = await import("./FilterChip");
      vi.spyOn(filterChipModule, "default").mockImplementation(mockFilterChip);
      ({ default: SidebarFilters } = await vi.importActual("./SidebarFilters"));
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    describe("rendering", () => {
      it("renders the search input", () => {
        render(<SidebarFilters {...defaultProps} />);
        expect(screen.getByPlaceholderText("Search devices, vendors, tags...")).toBeInTheDocument();
      });

      it("renders the Filters toggle button", () => {
        render(<SidebarFilters {...defaultProps} />);
        expect(screen.getByText("Filters")).toBeInTheDocument();
      });

      it("shows search query value", () => {
        render(<SidebarFilters {...defaultProps} searchQuery="test query" />);
        const input = screen.getByPlaceholderText("Search devices, vendors, tags...");
        expect(input).toHaveValue("test query");
      });

      it("shows clear search button when query exists", () => {
        render(<SidebarFilters {...defaultProps} searchQuery="test" />);
        const clearButton = document.querySelector(".fa-xmark")?.closest("button");
        expect(clearButton).toBeInTheDocument();
      });

      it("hides clear search button when query is empty", () => {
        render(<SidebarFilters {...defaultProps} searchQuery="" />);
        const searchContainer = screen.getByPlaceholderText("Search devices, vendors, tags...").parentElement;
        const clearButton = searchContainer?.querySelector(".fa-xmark");
        expect(clearButton).not.toBeInTheDocument();
      });
    });

    describe("search functionality", () => {
      it("calls onSearchChange when typing in search input", async () => {
        const user = userEvent.setup();
        const onSearchChange = vi.fn();
        render(<SidebarFilters {...defaultProps} onSearchChange={onSearchChange} />);

        const input = screen.getByPlaceholderText("Search devices, vendors, tags...");
        await user.type(input, "arista");

        expect(onSearchChange).toHaveBeenCalled();
      });

      it("calls onSearchChange with empty string when clear button is clicked", async () => {
        const user = userEvent.setup();
        const onSearchChange = vi.fn();
        render(<SidebarFilters {...defaultProps} searchQuery="test" onSearchChange={onSearchChange} />);

        const searchContainer = screen.getByPlaceholderText("Search devices, vendors, tags...").parentElement;
        const clearButton = searchContainer?.querySelector("button");
        if (clearButton) {
          await user.click(clearButton);
          expect(onSearchChange).toHaveBeenCalledWith("");
        }
      });
    });

    describe("filter panel expansion", () => {
      it("filter chips are hidden by default", () => {
        render(<SidebarFilters {...defaultProps} />);
        expect(screen.queryByText("Image Status")).not.toBeInTheDocument();
      });

      it("shows filter chips when expanded", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByText("Image Status")).toBeInTheDocument();
        expect(screen.getByText("Vendor")).toBeInTheDocument();
        expect(screen.getByText("Type")).toBeInTheDocument();
      });

      it("toggles expansion on click", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));
        expect(screen.getByText("Image Status")).toBeInTheDocument();

        await user.click(screen.getByText("Filters"));
        expect(screen.queryByText("Image Status")).not.toBeInTheDocument();
      });
    });

    describe("active filters indicator", () => {
      it("shows filter count when vendors are selected", () => {
        render(
          <SidebarFilters {...defaultProps} selectedVendors={new Set(["Arista"])} />
        );

        expect(screen.getByRole("button", { name: /filters 1/i })).toBeInTheDocument();
      });

      it("shows filter count when types are selected", () => {
        render(
          <SidebarFilters {...defaultProps} selectedTypes={new Set(["router"])} />
        );
        expect(screen.getByRole("button", { name: /filters 1/i })).toBeInTheDocument();
      });

      it("shows filter count when image status is not default", () => {
        render(<SidebarFilters {...defaultProps} imageStatus="no_image" />);
        expect(screen.getByRole("button", { name: /filters 1/i })).toBeInTheDocument();
      });

      it("shows combined filter count", () => {
        render(
          <SidebarFilters
            {...defaultProps}
            selectedVendors={new Set(["Arista", "Nokia"])}
            selectedTypes={new Set(["router"])}
            imageStatus="has_default"
          />
        );
        expect(screen.getByRole("button", { name: /filters 4/i })).toBeInTheDocument();
      });

      it("does not show filter count when no filters active", () => {
        render(<SidebarFilters {...defaultProps} />);
        expect(screen.getByRole("button", { name: /^filters$/i })).toBeInTheDocument();
      });
    });

    describe("image status filters", () => {
      it("renders image status filter chips when expanded", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByTestId("filter-chip-has-default")).toBeInTheDocument();
        expect(screen.getByTestId("filter-chip-has-image")).toBeInTheDocument();
        expect(screen.getByTestId("filter-chip-no-image")).toBeInTheDocument();
      });

      it("calls onImageStatusChange when Has Default is clicked", async () => {
        const user = userEvent.setup();
        const onImageStatusChange = vi.fn();
        render(
          <SidebarFilters {...defaultProps} onImageStatusChange={onImageStatusChange} />
        );

        await user.click(screen.getByText("Filters"));
        await user.click(screen.getByTestId("filter-chip-has-default"));

        expect(onImageStatusChange).toHaveBeenCalledWith("has_default");
      });

      it("toggles image status off when clicking active filter", async () => {
        const user = userEvent.setup();
        const onImageStatusChange = vi.fn();
        render(
          <SidebarFilters
            {...defaultProps}
            imageStatus="has_default"
            onImageStatusChange={onImageStatusChange}
          />
        );

        await user.click(screen.getByText("Filters"));
        await user.click(screen.getByTestId("filter-chip-has-default"));

        expect(onImageStatusChange).toHaveBeenCalledWith("all");
      });

      it("displays correct status counts", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByTestId("count-Has Default")).toHaveTextContent("1");
        expect(screen.getByTestId("count-Has Image")).toHaveTextContent("2");
        expect(screen.getByTestId("count-No Image")).toHaveTextContent("2");
      });
    });

    describe("vendor filters", () => {
      it("renders vendor filter chips when expanded", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByTestId("filter-chip-arista")).toBeInTheDocument();
        expect(screen.getByTestId("filter-chip-nokia")).toBeInTheDocument();
        expect(screen.getByTestId("filter-chip-generic")).toBeInTheDocument();
      });

      it("calls onVendorToggle when vendor chip is clicked", async () => {
        const user = userEvent.setup();
        const onVendorToggle = vi.fn();
        render(<SidebarFilters {...defaultProps} onVendorToggle={onVendorToggle} />);

        await user.click(screen.getByText("Filters"));
        await user.click(screen.getByTestId("filter-chip-arista"));

        expect(onVendorToggle).toHaveBeenCalledWith("Arista");
      });

      it("shows vendor count", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByTestId("count-Arista")).toHaveTextContent("2");
        expect(screen.getByTestId("count-Nokia")).toHaveTextContent("1");
      });

      it("limits displayed vendors to 8", async () => {
        const user = userEvent.setup();
        const manyVendorDevices = Array.from({ length: 12 }, (_, i) => ({
          id: `device-${i}`,
          name: `Device ${i}`,
          type: DeviceType.ROUTER,
          icon: "fa-server",
          versions: ["1.0"],
          isActive: true,
          vendor: `Vendor${i}`,
        }));

        render(<SidebarFilters {...defaultProps} devices={manyVendorDevices} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByText("+4 more")).toBeInTheDocument();
      });
    });

    describe("type filters", () => {
      it("renders type filter chips when expanded", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByTestId("filter-chip-routers")).toBeInTheDocument();
        expect(screen.getByTestId("filter-chip-switches")).toBeInTheDocument();
        expect(screen.getByTestId("filter-chip-hosts")).toBeInTheDocument();
      });

      it("calls onTypeToggle when type chip is clicked", async () => {
        const user = userEvent.setup();
        const onTypeToggle = vi.fn();
        render(<SidebarFilters {...defaultProps} onTypeToggle={onTypeToggle} />);

        await user.click(screen.getByText("Filters"));
        await user.click(screen.getByTestId("filter-chip-routers"));

        expect(onTypeToggle).toHaveBeenCalledWith("router");
      });

      it("shows type count", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByTestId("count-Routers")).toHaveTextContent("2");
        expect(screen.getByTestId("count-Switches")).toHaveTextContent("1");
        expect(screen.getByTestId("count-Hosts")).toHaveTextContent("1");
      });
    });

    describe("clear all filters", () => {
      it("shows clear all button when filters are active", async () => {
        const user = userEvent.setup();
        render(
          <SidebarFilters {...defaultProps} selectedVendors={new Set(["Arista"])} />
        );

        await user.click(screen.getByText("Filters"));

        expect(screen.getByText("Clear all filters")).toBeInTheDocument();
      });

      it("hides clear all button when no filters active", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.queryByText("Clear all filters")).not.toBeInTheDocument();
      });

      it("calls onClearAll when clear all button is clicked", async () => {
        const user = userEvent.setup();
        const onClearAll = vi.fn();
        render(
          <SidebarFilters
            {...defaultProps}
            selectedVendors={new Set(["Arista"])}
            onClearAll={onClearAll}
          />
        );

        await user.click(screen.getByText("Filters"));
        await user.click(screen.getByText("Clear all filters"));

        expect(onClearAll).toHaveBeenCalledTimes(1);
      });

      it("shows clear all for search query", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} searchQuery="test" />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByText("Clear all filters")).toBeInTheDocument();
      });
    });

    describe("edge cases", () => {
      it("handles empty devices list", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} devices={[]} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.queryByTestId("filter-chip-arista")).not.toBeInTheDocument();
      });

      it("handles devices without vendor", async () => {
        const user = userEvent.setup();
        const devicesWithoutVendor: DeviceModel[] = [
          {
            id: "test",
            name: "Test Device",
            type: DeviceType.ROUTER,
            icon: "fa-server",
            versions: ["1.0"],
            isActive: true,
            vendor: "",
          },
        ];

        render(<SidebarFilters {...defaultProps} devices={devicesWithoutVendor} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByText("Vendor")).toBeInTheDocument();
      });

      it("handles empty image library", async () => {
        const user = userEvent.setup();
        render(<SidebarFilters {...defaultProps} imageLibrary={[]} />);

        await user.click(screen.getByText("Filters"));

        expect(screen.getByTestId("count-No Image")).toHaveTextContent("4");
      });
    });
  });
}
