import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImageFilterBar, { ImageAssignmentFilter } from "./ImageFilterBar";
import { ImageLibraryEntry, DeviceModel, DeviceType } from "../types";

describe("ImageFilterBar", () => {
  const defaultImages: ImageLibraryEntry[] = [
    {
      id: "img-1",
      kind: "docker",
      reference: "ceos:4.28.0F",
      vendor: "Arista",
      device_id: null,
    },
    {
      id: "img-2",
      kind: "docker",
      reference: "ceos:4.29.0F",
      vendor: "Arista",
      device_id: "ceos",
    },
    {
      id: "img-3",
      kind: "qcow2",
      reference: "veos-4.28.qcow2",
      vendor: "Arista",
      device_id: null,
    },
    {
      id: "img-4",
      kind: "docker",
      reference: "sros:22.10.R1",
      vendor: "Nokia",
      device_id: "sros",
    },
    {
      id: "img-5",
      kind: "qcow2",
      reference: "junos-20.4R3.qcow2",
      vendor: "Juniper",
      device_id: null,
    },
  ];

  const defaultDevices: DeviceModel[] = [
    {
      id: "ceos",
      name: "Arista cEOS",
      type: DeviceType.ROUTER,
      icon: "fa-microchip",
      versions: ["4.28.0F"],
      isActive: true,
      vendor: "Arista",
    },
  ];

  const mockOnSearchChange = vi.fn();
  const mockOnVendorToggle = vi.fn();
  const mockOnKindToggle = vi.fn();
  const mockOnAssignmentFilterChange = vi.fn();
  const mockOnClearAll = vi.fn();

  const defaultProps = {
    images: defaultImages,
    devices: defaultDevices,
    searchQuery: "",
    onSearchChange: mockOnSearchChange,
    selectedVendors: new Set<string>(),
    onVendorToggle: mockOnVendorToggle,
    selectedKinds: new Set<string>(),
    onKindToggle: mockOnKindToggle,
    assignmentFilter: "all" as ImageAssignmentFilter,
    onAssignmentFilterChange: mockOnAssignmentFilterChange,
    onClearAll: mockOnClearAll,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("search functionality", () => {
    it("renders the search input", () => {
      render(<ImageFilterBar {...defaultProps} />);
      expect(
        screen.getByPlaceholderText("Search images by name, version, or vendor...")
      ).toBeInTheDocument();
    });

    it("displays current search query value", () => {
      render(<ImageFilterBar {...defaultProps} searchQuery="arista" />);
      const searchInput = screen.getByPlaceholderText(
        "Search images by name, version, or vendor..."
      );
      expect(searchInput).toHaveValue("arista");
    });

    it("calls onSearchChange when typing in search input", async () => {
      const user = userEvent.setup();
      render(<ImageFilterBar {...defaultProps} />);

      const searchInput = screen.getByPlaceholderText(
        "Search images by name, version, or vendor..."
      );
      await user.type(searchInput, "ceos");

      expect(mockOnSearchChange).toHaveBeenCalled();
    });

    it("shows clear button when search query is present", () => {
      render(<ImageFilterBar {...defaultProps} searchQuery="test" />);
      const clearButton = document.querySelector(".fa-xmark");
      expect(clearButton).toBeInTheDocument();
    });

    it("does not show clear button when search query is empty", () => {
      render(<ImageFilterBar {...defaultProps} searchQuery="" />);
      // The clear button should not be present (only the one in the filter bar footer)
      const searchContainer = screen.getByPlaceholderText(
        "Search images by name, version, or vendor..."
      ).parentElement;
      const clearButton = searchContainer?.querySelector(".fa-xmark");
      expect(clearButton).not.toBeInTheDocument();
    });

    it("clears search when clear button is clicked", async () => {
      const user = userEvent.setup();
      render(<ImageFilterBar {...defaultProps} searchQuery="test" />);

      const searchContainer = screen.getByPlaceholderText(
        "Search images by name, version, or vendor..."
      ).parentElement;
      const clearButton = searchContainer?.querySelector(".fa-xmark")?.closest("button");
      expect(clearButton).toBeInTheDocument();

      await user.click(clearButton!);
      expect(mockOnSearchChange).toHaveBeenCalledWith("");
    });
  });

  describe("assignment status filters", () => {
    it("renders all assignment filter options", () => {
      render(<ImageFilterBar {...defaultProps} />);

      expect(screen.getByText("All")).toBeInTheDocument();
      expect(screen.getByText("Unassigned")).toBeInTheDocument();
      expect(screen.getByText("Assigned")).toBeInTheDocument();
    });

    it("displays correct counts for assignment filters", () => {
      render(<ImageFilterBar {...defaultProps} />);

      // Total: 5, Unassigned: 3, Assigned: 2
      const allButton = screen.getByText("All").closest("button");
      const unassignedButton = screen.getByText("Unassigned").closest("button");
      const assignedButton = screen.getByText("Assigned").closest("button");

      expect(allButton).toHaveTextContent("5");
      expect(unassignedButton).toHaveTextContent("3");
      expect(assignedButton).toHaveTextContent("2");
    });

    it("calls onAssignmentFilterChange when clicking All", async () => {
      const user = userEvent.setup();
      render(
        <ImageFilterBar {...defaultProps} assignmentFilter="unassigned" />
      );

      await user.click(screen.getByText("All"));
      expect(mockOnAssignmentFilterChange).toHaveBeenCalledWith("all");
    });

    it("calls onAssignmentFilterChange when clicking Unassigned", async () => {
      const user = userEvent.setup();
      render(<ImageFilterBar {...defaultProps} />);

      await user.click(screen.getByText("Unassigned"));
      expect(mockOnAssignmentFilterChange).toHaveBeenCalledWith("unassigned");
    });

    it("calls onAssignmentFilterChange when clicking Assigned", async () => {
      const user = userEvent.setup();
      render(<ImageFilterBar {...defaultProps} />);

      await user.click(screen.getByText("Assigned"));
      expect(mockOnAssignmentFilterChange).toHaveBeenCalledWith("assigned");
    });

    it("shows Status label", () => {
      render(<ImageFilterBar {...defaultProps} />);
      expect(screen.getByText("Status:")).toBeInTheDocument();
    });
  });

  describe("type/kind filters", () => {
    it("renders all unique kinds from images", () => {
      render(<ImageFilterBar {...defaultProps} />);

      expect(screen.getByText("Docker")).toBeInTheDocument();
      expect(screen.getByText("QCOW2")).toBeInTheDocument();
    });

    it("shows Type label", () => {
      render(<ImageFilterBar {...defaultProps} />);
      expect(screen.getByText("Type:")).toBeInTheDocument();
    });

    it("calls onKindToggle when clicking a kind filter", async () => {
      const user = userEvent.setup();
      render(<ImageFilterBar {...defaultProps} />);

      await user.click(screen.getByText("Docker"));
      expect(mockOnKindToggle).toHaveBeenCalledWith("docker");
    });

    it("calls onKindToggle for qcow2 kind", async () => {
      const user = userEvent.setup();
      render(<ImageFilterBar {...defaultProps} />);

      await user.click(screen.getByText("QCOW2"));
      expect(mockOnKindToggle).toHaveBeenCalledWith("qcow2");
    });

    it("renders unknown kind labels correctly", () => {
      const imagesWithUnknownKind: ImageLibraryEntry[] = [
        {
          id: "img-1",
          kind: "vmdk",
          reference: "test.vmdk",
          device_id: null,
        },
      ];
      render(<ImageFilterBar {...defaultProps} images={imagesWithUnknownKind} />);
      expect(screen.getByText("vmdk")).toBeInTheDocument();
    });
  });

  describe("vendor filters", () => {
    it("renders vendor filters when vendors exist", () => {
      render(<ImageFilterBar {...defaultProps} />);

      expect(screen.getByText("Vendor:")).toBeInTheDocument();
      expect(screen.getByText("Arista")).toBeInTheDocument();
      expect(screen.getByText("Juniper")).toBeInTheDocument();
      expect(screen.getByText("Nokia")).toBeInTheDocument();
    });

    it("does not render vendor section when no vendors", () => {
      const imagesWithoutVendors: ImageLibraryEntry[] = [
        {
          id: "img-1",
          kind: "docker",
          reference: "test",
          device_id: null,
        },
      ];
      render(
        <ImageFilterBar {...defaultProps} images={imagesWithoutVendors} />
      );
      expect(screen.queryByText("Vendor:")).not.toBeInTheDocument();
    });

    it("calls onVendorToggle when clicking a vendor filter", async () => {
      const user = userEvent.setup();
      render(<ImageFilterBar {...defaultProps} />);

      await user.click(screen.getByText("Arista"));
      expect(mockOnVendorToggle).toHaveBeenCalledWith("Arista");
    });

    it("limits vendors to 5 and shows overflow count", () => {
      const imagesWithManyVendors: ImageLibraryEntry[] = [
        { id: "1", kind: "docker", reference: "a", vendor: "Vendor1", device_id: null },
        { id: "2", kind: "docker", reference: "b", vendor: "Vendor2", device_id: null },
        { id: "3", kind: "docker", reference: "c", vendor: "Vendor3", device_id: null },
        { id: "4", kind: "docker", reference: "d", vendor: "Vendor4", device_id: null },
        { id: "5", kind: "docker", reference: "e", vendor: "Vendor5", device_id: null },
        { id: "6", kind: "docker", reference: "f", vendor: "Vendor6", device_id: null },
        { id: "7", kind: "docker", reference: "g", vendor: "Vendor7", device_id: null },
      ];
      render(
        <ImageFilterBar {...defaultProps} images={imagesWithManyVendors} />
      );

      // Should show +2 for the overflow
      expect(screen.getByText("+2")).toBeInTheDocument();
    });

    it("does not show overflow count when 5 or fewer vendors", () => {
      render(<ImageFilterBar {...defaultProps} />);
      // Only 3 vendors in default images
      expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
    });
  });

  describe("clear all filters", () => {
    it("shows Clear filters button when search query is present", () => {
      render(<ImageFilterBar {...defaultProps} searchQuery="test" />);
      expect(screen.getByText("Clear filters")).toBeInTheDocument();
    });

    it("shows Clear filters button when vendors are selected", () => {
      render(
        <ImageFilterBar
          {...defaultProps}
          selectedVendors={new Set(["Arista"])}
        />
      );
      expect(screen.getByText("Clear filters")).toBeInTheDocument();
    });

    it("shows Clear filters button when kinds are selected", () => {
      render(
        <ImageFilterBar
          {...defaultProps}
          selectedKinds={new Set(["docker"])}
        />
      );
      expect(screen.getByText("Clear filters")).toBeInTheDocument();
    });

    it("shows Clear filters button when assignment filter is not all", () => {
      render(
        <ImageFilterBar {...defaultProps} assignmentFilter="unassigned" />
      );
      expect(screen.getByText("Clear filters")).toBeInTheDocument();
    });

    it("does not show Clear filters button when no filters active", () => {
      render(<ImageFilterBar {...defaultProps} />);
      expect(screen.queryByText("Clear filters")).not.toBeInTheDocument();
    });

    it("calls onClearAll when Clear filters is clicked", async () => {
      const user = userEvent.setup();
      render(<ImageFilterBar {...defaultProps} searchQuery="test" />);

      await user.click(screen.getByText("Clear filters"));
      expect(mockOnClearAll).toHaveBeenCalled();
    });
  });

  describe("active state styling", () => {
    it("shows active style for selected assignment filter", () => {
      render(
        <ImageFilterBar {...defaultProps} assignmentFilter="unassigned" />
      );
      const unassignedButton = screen.getByText("Unassigned").closest("button");
      expect(unassignedButton).toHaveClass("bg-sage-600");
    });

    it("shows active style for selected kind filter", () => {
      render(
        <ImageFilterBar
          {...defaultProps}
          selectedKinds={new Set(["docker"])}
        />
      );
      const dockerButton = screen.getByText("Docker").closest("button");
      expect(dockerButton).toHaveClass("bg-sage-600");
    });

    it("shows active style for selected vendor filter", () => {
      render(
        <ImageFilterBar
          {...defaultProps}
          selectedVendors={new Set(["Arista"])}
        />
      );
      const aristaButton = screen.getByText("Arista").closest("button");
      expect(aristaButton).toHaveClass("bg-sage-600");
    });

    it("shows inactive style for non-selected filters", () => {
      render(<ImageFilterBar {...defaultProps} />);
      const dockerButton = screen.getByText("Docker").closest("button");
      expect(dockerButton).not.toHaveClass("bg-sage-600");
      expect(dockerButton).toHaveClass("bg-stone-100");
    });
  });

  describe("dividers", () => {
    it("renders divider between status and type sections", () => {
      render(<ImageFilterBar {...defaultProps} />);
      // Check for divider elements (w-px class)
      const dividers = document.querySelectorAll(".w-px");
      expect(dividers.length).toBeGreaterThan(0);
    });

    it("renders divider before vendor section when vendors exist", () => {
      render(<ImageFilterBar {...defaultProps} />);
      // Should have 2 dividers: after status, after type (before vendors)
      const dividers = document.querySelectorAll(".w-px.bg-stone-200");
      expect(dividers.length).toBe(2);
    });
  });

  describe("computed values", () => {
    it("sorts vendors alphabetically", () => {
      render(<ImageFilterBar {...defaultProps} />);

      const vendorChips = screen.getAllByText(/Arista|Juniper|Nokia/);
      const vendorNames = vendorChips.map((el) => el.textContent);

      // Should be sorted alphabetically
      expect(vendorNames).toEqual(["Arista", "Juniper", "Nokia"]);
    });

    it("sorts kinds alphabetically", () => {
      render(<ImageFilterBar {...defaultProps} />);

      // Docker should come before QCOW2 alphabetically (docker < qcow2)
      const buttons = screen.getAllByRole("button");
      const dockerIndex = buttons.findIndex((b) =>
        b.textContent?.includes("Docker")
      );
      const qcow2Index = buttons.findIndex((b) =>
        b.textContent?.includes("QCOW2")
      );

      expect(dockerIndex).toBeLessThan(qcow2Index);
    });

    it("recalculates counts when images change", () => {
      const { rerender } = render(<ImageFilterBar {...defaultProps} />);
      // Find the "All" button and check its count
      const allButton = screen.getByText("All").closest("button");
      expect(allButton).toHaveTextContent("5");

      const newImages: ImageLibraryEntry[] = [
        { id: "1", kind: "docker", reference: "test", device_id: null },
        { id: "2", kind: "docker", reference: "test2", device_id: null },
      ];

      rerender(<ImageFilterBar {...defaultProps} images={newImages} />);
      const updatedAllButton = screen.getByText("All").closest("button");
      expect(updatedAllButton).toHaveTextContent("2");
    });
  });

  describe("edge cases", () => {
    it("handles empty images array", () => {
      render(<ImageFilterBar {...defaultProps} images={[]} />);

      expect(screen.getByText("All")).toBeInTheDocument();
      // Check All button has count 0
      const allButton = screen.getByText("All").closest("button");
      expect(allButton).toHaveTextContent("0");
    });

    it("handles images with null vendors", () => {
      const imagesWithNullVendor: ImageLibraryEntry[] = [
        { id: "1", kind: "docker", reference: "test", vendor: null, device_id: null },
        { id: "2", kind: "docker", reference: "test2", vendor: "Arista", device_id: null },
      ];
      render(
        <ImageFilterBar {...defaultProps} images={imagesWithNullVendor} />
      );

      // Should only show Arista, not null
      expect(screen.getByText("Arista")).toBeInTheDocument();
      expect(screen.queryByText("null")).not.toBeInTheDocument();
    });

    it("handles single image", () => {
      const singleImage: ImageLibraryEntry[] = [
        { id: "1", kind: "docker", reference: "test", vendor: "Test", device_id: null },
      ];
      render(<ImageFilterBar {...defaultProps} images={singleImage} />);

      // Check All button has count 1
      const allButton = screen.getByText("All").closest("button");
      expect(allButton).toHaveTextContent("1");
      expect(screen.getByText("Docker")).toBeInTheDocument();
      expect(screen.getByText("Test")).toBeInTheDocument();
    });
  });
});
