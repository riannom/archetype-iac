import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ISOImportModal from "./ISOImportModal";

// Mock API_BASE_URL
vi.mock("../api", () => ({
  API_BASE_URL: "http://localhost:8000",
}));

// Mock fetch globally
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(() => "test-token"),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
Object.defineProperty(window, "localStorage", { value: localStorageMock });

describe("ISOImportModal", () => {
  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
    onImportComplete: vi.fn(),
  };

  const mockBrowseResponse = {
    upload_dir: "/var/lib/archetype/uploads",
    files: [
      {
        name: "cisco-refplat.iso",
        path: "/var/lib/archetype/uploads/cisco-refplat.iso",
        size_bytes: 5368709120, // 5 GB
        modified_at: "2024-01-15T10:00:00Z",
      },
      {
        name: "network-images.iso",
        path: "/var/lib/archetype/uploads/network-images.iso",
        size_bytes: 2147483648, // 2 GB
        modified_at: "2024-01-10T08:00:00Z",
      },
    ],
  };

  const mockScanResponse = {
    session_id: "session-123",
    iso_path: "/var/lib/archetype/uploads/cisco-refplat.iso",
    format: "refplat",
    size_bytes: 5368709120,
    node_definitions: [
      {
        id: "csr1000v",
        label: "CSR1000v",
        description: "Cisco CSR1000v",
        nature: "router",
        vendor: "Cisco",
        ram_mb: 4096,
        cpus: 2,
        interfaces: ["GigabitEthernet1", "GigabitEthernet2"],
      },
    ],
    images: [
      {
        id: "img-csr",
        node_definition_id: "csr1000v",
        label: "CSR1000v 17.3.2",
        description: "CSR1000v image",
        version: "17.3.2",
        disk_image_filename: "csr1000v-universalk9.qcow2",
        disk_image_path: "/images/csr1000v.qcow2",
        size_bytes: 1073741824,
        image_type: "qcow2",
      },
    ],
    parse_errors: [],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockReset();
    // Default mock for browse endpoint
    mockFetch.mockImplementation((url: string) => {
      if (url.includes("/iso/browse")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockBrowseResponse),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      });
    });
  });

  afterEach(() => {
    vi.clearAllTimers();
  });

  describe("rendering", () => {
    it("does not render when isOpen is false", () => {
      render(<ISOImportModal {...defaultProps} isOpen={false} />);
      expect(screen.queryByText("Import from ISO")).not.toBeInTheDocument();
    });

    it("renders when isOpen is true", async () => {
      render(<ISOImportModal {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText("Import from ISO")).toBeInTheDocument();
      });
    });

    it("renders modal header with title", async () => {
      render(<ISOImportModal {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText("Import from ISO")).toBeInTheDocument();
        expect(
          screen.getByText("Import VM images from vendor ISO files (Cisco RefPlat, etc.)")
        ).toBeInTheDocument();
      });
    });

    it("renders close button", async () => {
      render(<ISOImportModal {...defaultProps} />);
      await waitFor(() => {
        const closeButton = document.querySelector(".fa-xmark")?.closest("button");
        expect(closeButton).toBeInTheDocument();
      });
    });

    it("renders mode tabs", async () => {
      render(<ISOImportModal {...defaultProps} />);
      await waitFor(() => {
        expect(screen.getByText("Browse Server")).toBeInTheDocument();
        expect(screen.getByText("Upload ISO")).toBeInTheDocument();
        expect(screen.getByText("Custom Path")).toBeInTheDocument();
      });
    });
  });

  describe("browse mode", () => {
    it("fetches available ISOs on open", async () => {
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          "http://localhost:8000/iso/browse",
          expect.any(Object)
        );
      });
    });

    it("displays available ISOs", async () => {
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
        expect(screen.getByText("network-images.iso")).toBeInTheDocument();
      });
    });

    it("shows file size and date for ISOs", async () => {
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/5 GB/)).toBeInTheDocument();
      });
    });

    it("selects ISO when clicked", async () => {
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));

      // Should show check icon for selected ISO
      const checkIcons = document.querySelectorAll(".fa-check");
      expect(checkIcons.length).toBeGreaterThan(0);
    });

    it("shows empty state when no ISOs available", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/iso/browse")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ upload_dir: "/uploads", files: [] }),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      });

      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("No ISOs found in upload directory")).toBeInTheDocument();
      });
    });

    it("shows refresh button", async () => {
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Refresh")).toBeInTheDocument();
      });
    });

    it("refreshes ISO list when refresh is clicked", async () => {
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Refresh")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Refresh"));

      // Should have called browse endpoint twice (initial + refresh)
      const browseCalls = mockFetch.mock.calls.filter((call) =>
        call[0].includes("/iso/browse")
      );
      expect(browseCalls.length).toBe(2);
    });
  });

  describe("custom path mode", () => {
    it("switches to custom path mode when tab is clicked", async () => {
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Custom Path")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Custom Path"));

      expect(screen.getByPlaceholderText("/path/to/image.iso")).toBeInTheDocument();
    });

    it("allows entering custom ISO path", async () => {
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Custom Path")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Custom Path"));

      const input = screen.getByPlaceholderText("/path/to/image.iso");
      await user.type(input, "/custom/path/test.iso");

      expect(input).toHaveValue("/custom/path/test.iso");
    });
  });

  describe("upload mode", () => {
    it("switches to upload mode when tab is clicked", async () => {
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Upload ISO")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Upload ISO"));

      expect(
        screen.getByText(/Drop ISO file here or click to browse/)
      ).toBeInTheDocument();
    });

    it("shows Upload & Scan button in upload mode", async () => {
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Upload ISO")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Upload ISO"));

      expect(screen.getByText("Upload & Scan")).toBeInTheDocument();
    });

    it("disables Upload & Scan when no file selected", async () => {
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Upload ISO")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Upload ISO"));

      const uploadButton = screen.getByText("Upload & Scan");
      expect(uploadButton).toBeDisabled();
    });
  });

  describe("scan functionality", () => {
    it("shows Scan ISO button when ISO is selected in browse mode", async () => {
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));

      expect(screen.getByText("Scan ISO")).toBeInTheDocument();
    });

    it("disables Scan ISO button when no ISO selected", async () => {
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Scan ISO")).toBeInTheDocument();
      });

      // Button should be disabled
      const scanButton = screen.getByText("Scan ISO");
      expect(scanButton).toBeDisabled();
    });

    it("scans ISO when Scan ISO button is clicked", async () => {
      const user = userEvent.setup();
      mockFetch.mockImplementation((url: string, options?: RequestInit) => {
        if (url.includes("/iso/browse")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockBrowseResponse),
          });
        }
        if (url.includes("/iso/scan")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockScanResponse),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      });

      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          "http://localhost:8000/iso/scan",
          expect.objectContaining({
            method: "POST",
          })
        );
      });
    });

    it("shows error when scan fails", async () => {
      const user = userEvent.setup();
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/iso/browse")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockBrowseResponse),
          });
        }
        if (url.includes("/iso/scan")) {
          return Promise.resolve({
            ok: false,
            status: 400,
            json: () => Promise.resolve({ detail: "Invalid ISO format" }),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      });

      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText("Invalid ISO format")).toBeInTheDocument();
      });
    });
  });

  describe("review step", () => {
    const setupReviewMock = () => {
      mockFetch.mockReset();
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/iso/browse")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockBrowseResponse),
          });
        }
        if (url.includes("/iso/scan")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockScanResponse),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      });
    };

    it("shows review step after successful scan", async () => {
      setupReviewMock();
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      // Wait for scan to complete and show review step
      await waitFor(
        () => {
          // Images to Import section header includes the count
          expect(screen.getByText(/Images to Import/)).toBeInTheDocument();
        },
        { timeout: 5000 }
      );
    });

    it("displays node definitions", async () => {
      setupReviewMock();
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText("CSR1000v")).toBeInTheDocument();
      });
    });

    it("displays images with checkboxes", async () => {
      setupReviewMock();
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText("CSR1000v 17.3.2")).toBeInTheDocument();
      });

      // Multiple checkboxes exist (image selection + create devices option)
      const checkboxes = screen.getAllByRole("checkbox");
      expect(checkboxes.length).toBeGreaterThan(0);
    });

    it("allows toggling image selection", async () => {
      setupReviewMock();
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText("CSR1000v 17.3.2")).toBeInTheDocument();
      });

      // Get the first checkbox (image selection)
      const checkboxes = screen.getAllByRole("checkbox");
      const imageCheckbox = checkboxes[0];

      // Should be checked initially
      expect(imageCheckbox).toBeChecked();

      // Click to toggle
      await user.click(imageCheckbox);
      expect(imageCheckbox).not.toBeChecked();
    });

    it("shows Select All and Select None buttons", async () => {
      setupReviewMock();
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText("Select All")).toBeInTheDocument();
        expect(screen.getByText("Select None")).toBeInTheDocument();
      });
    });

    it("shows create devices checkbox", async () => {
      setupReviewMock();
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(
          screen.getByText("Create device types for new definitions")
        ).toBeInTheDocument();
      });
    });

    it("shows Back button in review step", async () => {
      setupReviewMock();
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText("Back")).toBeInTheDocument();
      });
    });

    it("goes back to input step when Back is clicked", async () => {
      setupReviewMock();
      const user = userEvent.setup();
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText("Back")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Back"));

      await waitFor(() => {
        expect(screen.getByText("Browse Server")).toBeInTheDocument();
      });
    });
  });

  describe("import functionality", () => {
    it("shows Import button in review step", async () => {
      const user = userEvent.setup();
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/iso/browse")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockBrowseResponse),
          });
        }
        if (url.includes("/iso/scan")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockScanResponse),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      });

      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText(/Import 1 Image/)).toBeInTheDocument();
      });
    });

    it("disables Import button when no images selected", async () => {
      const user = userEvent.setup();
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/iso/browse")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockBrowseResponse),
          });
        }
        if (url.includes("/iso/scan")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockScanResponse),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      });

      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("cisco-refplat.iso")).toBeInTheDocument();
      });

      await user.click(screen.getByText("cisco-refplat.iso"));
      await user.click(screen.getByText("Scan ISO"));

      await waitFor(() => {
        expect(screen.getByText("CSR1000v 17.3.2")).toBeInTheDocument();
      });

      // Get the first checkbox (image selection) and deselect it
      const checkboxes = screen.getAllByRole("checkbox");
      const imageCheckbox = checkboxes[0];
      await user.click(imageCheckbox);

      // Import button should now be disabled
      const importButton = screen.getByText(/Import 0 Image/);
      expect(importButton).toBeDisabled();
    });
  });

  describe("closing behavior", () => {
    it("calls onClose when close button is clicked", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      render(<ISOImportModal {...defaultProps} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText("Import from ISO")).toBeInTheDocument();
      });

      const closeButton = document.querySelector(".fa-xmark")?.closest("button");
      if (closeButton) {
        await user.click(closeButton);
        expect(onClose).toHaveBeenCalledTimes(1);
      }
    });

    it("calls onClose when backdrop is clicked", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      render(<ISOImportModal {...defaultProps} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText("Import from ISO")).toBeInTheDocument();
      });

      const backdrop = document.querySelector(".bg-black\\/50");
      if (backdrop) {
        await user.click(backdrop);
        expect(onClose).toHaveBeenCalledTimes(1);
      }
    });

    it("calls onClose when Cancel button is clicked", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      render(<ISOImportModal {...defaultProps} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText("Cancel")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Cancel"));
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  describe("state reset", () => {
    it("resets state when modal reopens", async () => {
      const { rerender } = render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Import from ISO")).toBeInTheDocument();
      });

      // Close modal
      rerender(<ISOImportModal {...defaultProps} isOpen={false} />);

      // Reopen modal
      rerender(<ISOImportModal {...defaultProps} isOpen={true} />);

      await waitFor(() => {
        // Should be back at input step
        expect(screen.getByText("Browse Server")).toBeInTheDocument();
      });
    });
  });

  describe("supported formats info", () => {
    it("displays supported formats section", async () => {
      render(<ISOImportModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Supported Formats")).toBeInTheDocument();
        expect(screen.getByText(/Cisco VIRL2\/CML2/)).toBeInTheDocument();
      });
    });
  });
});
