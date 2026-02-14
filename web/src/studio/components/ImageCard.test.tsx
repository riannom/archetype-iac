import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImageCard from "./ImageCard";
import { ImageLibraryEntry, ImageHostStatus } from "../types";

// Mock the DragContext
const mockStartDrag = vi.fn();
const mockEndDrag = vi.fn();
const mockDragState = {
  isDragging: false,
  draggedImageId: null,
  draggedImageData: null,
  dragOverDeviceId: null,
  isValidTarget: false,
};

vi.mock("../contexts/DragContext", () => ({
  useDragContext: () => ({
    dragState: mockDragState,
    startDrag: mockStartDrag,
    endDrag: mockEndDrag,
  }),
  useDragHandlers: () => ({
    handleDragStart: vi.fn(),
    handleDragEnd: vi.fn(),
  }),
}));

// Mock the api module
vi.mock("../../api", () => ({
  apiRequest: vi.fn(),
}));

// Mock window.alert and window.confirm
const mockAlert = vi.fn();
const mockConfirm = vi.fn();
window.alert = mockAlert;
window.confirm = mockConfirm;

// Import the mocked apiRequest after mocking
import { apiRequest } from "../../api";
const mockApiRequest = vi.mocked(apiRequest);

describe("ImageCard", () => {
  const defaultImage: ImageLibraryEntry = {
    id: "img-123",
    kind: "docker",
    reference: "ceos:4.28.0F",
    filename: "ceos-4.28.0F.tar",
    device_id: null,
    version: "4.28.0F",
    vendor: "Arista",
    uploaded_at: "2024-01-15T10:30:00Z",
    size_bytes: 2147483648, // 2GB
    is_default: false,
    notes: "Production image for cEOS",
  };

  const mockOnUnassign = vi.fn();
  const mockOnSetDefault = vi.fn();
  const mockOnDelete = vi.fn();
  const mockOnSync = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockDragState.isDragging = false;
    mockDragState.draggedImageId = null;
    mockConfirm.mockReturnValue(true);
    mockApiRequest.mockResolvedValue({});
  });

  describe("rendering", () => {
    it("renders the image filename", () => {
      render(<ImageCard image={defaultImage} />);
      expect(screen.getByText("ceos-4.28.0F.tar")).toBeInTheDocument();
    });

    it("renders the reference when no filename provided", () => {
      const imageWithoutFilename = { ...defaultImage, filename: undefined };
      render(<ImageCard image={imageWithoutFilename} />);
      expect(screen.getByText("ceos:4.28.0F")).toBeInTheDocument();
    });

    it("displays the image kind", () => {
      render(<ImageCard image={defaultImage} />);
      // The kind is displayed as uppercase via CSS class, but text content is lowercase
      expect(screen.getByText("docker")).toBeInTheDocument();
    });

    it("displays the image size in tooltip", () => {
      render(<ImageCard image={defaultImage} />);
      // Size is now shown in tooltip, not directly
      const card = screen.getByText("ceos-4.28.0F.tar").closest("[title]");
      expect(card?.getAttribute("title")).toContain("Size: 2.0 GB");
    });

    it("displays the vendor in tooltip", () => {
      render(<ImageCard image={defaultImage} />);
      // Vendor is now shown in tooltip, not directly
      const card = screen.getByText("ceos-4.28.0F.tar").closest("[title]");
      expect(card?.getAttribute("title")).toContain("Vendor: Arista");
    });

    it("displays the version", () => {
      render(<ImageCard image={defaultImage} />);
      expect(screen.getByText("4.28.0F")).toBeInTheDocument();
    });

    it("displays the upload date", () => {
      render(<ImageCard image={defaultImage} />);
      expect(screen.getByText("Jan 15, 2024")).toBeInTheDocument();
    });

    it("displays notes in tooltip when provided", () => {
      render(<ImageCard image={defaultImage} />);
      // Notes are now shown in tooltip, not directly
      const card = screen.getByText("ceos-4.28.0F.tar").closest("[title]");
      expect(card?.getAttribute("title")).toContain("Notes: Production image for cEOS");
    });

    it("displays DEFAULT badge when is_default is true", () => {
      const defaultImageWithBadge = { ...defaultImage, is_default: true };
      render(<ImageCard image={defaultImageWithBadge} />);
      expect(screen.getByText("DEFAULT")).toBeInTheDocument();
    });

    it("does not display DEFAULT badge when is_default is false", () => {
      render(<ImageCard image={defaultImage} />);
      expect(screen.queryByText("DEFAULT")).not.toBeInTheDocument();
    });

    it("displays device_id when set", () => {
      const assignedImage = { ...defaultImage, device_id: "ceos" };
      render(<ImageCard image={assignedImage} />);
      // Device ID is shown inline in the metadata row
      expect(screen.getByText("ceos")).toBeInTheDocument();
    });
  });

  describe("image kinds", () => {
    it("shows docker icon for docker images", () => {
      render(<ImageCard image={defaultImage} />);
      const icon = document.querySelector(".fa-docker");
      expect(icon).toBeInTheDocument();
    });

    it("shows hard drive icon for qcow2 images", () => {
      const qcow2Image = { ...defaultImage, kind: "qcow2" };
      render(<ImageCard image={qcow2Image} />);
      const icon = document.querySelector(".fa-hard-drive");
      expect(icon).toBeInTheDocument();
    });

    it("applies blue color for docker images", () => {
      render(<ImageCard image={defaultImage} />);
      const icon = document.querySelector(".fa-docker.text-blue-500");
      expect(icon).toBeInTheDocument();
    });

    it("applies orange color for qcow2 images", () => {
      const qcow2Image = { ...defaultImage, kind: "qcow2" };
      render(<ImageCard image={qcow2Image} />);
      const icon = document.querySelector(".fa-hard-drive.text-orange-500");
      expect(icon).toBeInTheDocument();
    });
  });

  describe("compact mode", () => {
    it("renders compact version when compact prop is true", () => {
      render(<ImageCard image={defaultImage} compact />);
      // Compact mode should show filename but not detailed metadata
      expect(screen.getByText("ceos-4.28.0F.tar")).toBeInTheDocument();
      // Should not show the detailed kind text in compact mode
      expect(screen.queryByText("DOCKER")).not.toBeInTheDocument();
    });

    it("shows version in compact mode", () => {
      render(<ImageCard image={defaultImage} compact />);
      expect(screen.getByText("4.28.0F")).toBeInTheDocument();
    });

    it("shows grip icon on hover in compact mode", () => {
      render(<ImageCard image={defaultImage} compact />);
      const gripIcon = document.querySelector(".fa-grip-vertical");
      expect(gripIcon).toBeInTheDocument();
    });
  });

  describe("drag functionality", () => {
    it("is draggable", () => {
      render(<ImageCard image={defaultImage} />);
      const card = screen.getByText("ceos-4.28.0F.tar").closest("[draggable]");
      expect(card).toHaveAttribute("draggable", "true");
    });

    it("applies dragging styles when being dragged", () => {
      mockDragState.isDragging = true;
      mockDragState.draggedImageId = "img-123";
      render(<ImageCard image={defaultImage} />);
      const card = screen.getByText("ceos-4.28.0F.tar").closest("[draggable]");
      expect(card).toHaveClass("opacity-50");
      expect(card).toHaveClass("scale-95");
    });
  });

  describe("sync status", () => {
    const createHostStatus = (statuses: Array<{ status: string; host_id: string }>): ImageHostStatus[] => {
      return statuses.map(({ status, host_id }) => ({
        host_id,
        host_name: `Host ${host_id}`,
        status: status as ImageHostStatus["status"],
        size_bytes: null,
        synced_at: null,
        error_message: null,
      }));
    };

    it("does not show sync status by default", () => {
      const imageWithStatus = {
        ...defaultImage,
        host_status: createHostStatus([
          { status: "synced", host_id: "1" },
          { status: "synced", host_id: "2" },
        ]),
      };
      render(<ImageCard image={imageWithStatus} />);
      expect(screen.queryByText("All synced")).not.toBeInTheDocument();
    });

    it("shows all synced status when showSyncStatus is true", () => {
      const imageWithStatus = {
        ...defaultImage,
        host_status: createHostStatus([
          { status: "synced", host_id: "1" },
          { status: "synced", host_id: "2" },
        ]),
      };
      render(<ImageCard image={imageWithStatus} showSyncStatus />);
      expect(screen.getByTitle("Agent sync: All synced")).toBeInTheDocument();
    });

    it("shows syncing status when any host is syncing", () => {
      const imageWithStatus = {
        ...defaultImage,
        host_status: createHostStatus([
          { status: "syncing", host_id: "1" },
          { status: "synced", host_id: "2" },
        ]),
      };
      render(<ImageCard image={imageWithStatus} showSyncStatus />);
      expect(screen.getByTitle("Agent sync: Syncing")).toBeInTheDocument();
    });

    it("shows failed count when any host has failed", () => {
      const imageWithStatus = {
        ...defaultImage,
        host_status: createHostStatus([
          { status: "failed", host_id: "1" },
          { status: "synced", host_id: "2" },
        ]),
      };
      render(<ImageCard image={imageWithStatus} showSyncStatus />);
      expect(screen.getByTitle("Agent sync: 1 failed")).toBeInTheDocument();
    });

    it("shows partial sync count", () => {
      const imageWithStatus = {
        ...defaultImage,
        host_status: createHostStatus([
          { status: "synced", host_id: "1" },
          { status: "missing", host_id: "2" },
          { status: "missing", host_id: "3" },
        ]),
      };
      render(<ImageCard image={imageWithStatus} showSyncStatus />);
      expect(screen.getByTitle("Agent sync: 1/3")).toBeInTheDocument();
    });

    it("shows unknown status when no synced, syncing, or failed hosts", () => {
      const imageWithStatus = {
        ...defaultImage,
        host_status: createHostStatus([
          { status: "unknown", host_id: "1" },
        ]),
      };
      render(<ImageCard image={imageWithStatus} showSyncStatus />);
      expect(screen.getByTitle("Agent sync: Unknown")).toBeInTheDocument();
    });
  });

  describe("action buttons", () => {
    it("shows set default button when not default and handler provided", () => {
      const assignedImage = { ...defaultImage, device_id: "ceos", is_default: false };
      render(<ImageCard image={assignedImage} onSetDefault={mockOnSetDefault} />);
      const setDefaultButton = screen.getByTitle("Set as default");
      expect(setDefaultButton).toBeInTheDocument();
    });

    it("does not show set default button when already default", () => {
      const assignedImage = { ...defaultImage, device_id: "ceos", is_default: true };
      render(<ImageCard image={assignedImage} onSetDefault={mockOnSetDefault} />);
      expect(screen.queryByTitle("Set as default")).not.toBeInTheDocument();
    });

    it("does not show set default button without device_id", () => {
      render(<ImageCard image={defaultImage} onSetDefault={mockOnSetDefault} />);
      expect(screen.queryByTitle("Set as default")).not.toBeInTheDocument();
    });

    it("calls onSetDefault when clicked", async () => {
      const user = userEvent.setup();
      const assignedImage = { ...defaultImage, device_id: "ceos", is_default: false };
      render(<ImageCard image={assignedImage} onSetDefault={mockOnSetDefault} />);

      await user.click(screen.getByTitle("Set as default"));
      expect(mockOnSetDefault).toHaveBeenCalled();
    });

    it("shows unassign button when handler provided", () => {
      render(<ImageCard image={defaultImage} onUnassign={mockOnUnassign} />);
      const unassignButton = screen.getByTitle("Unassign from device");
      expect(unassignButton).toBeInTheDocument();
    });

    it("calls onUnassign when clicked", async () => {
      const user = userEvent.setup();
      render(<ImageCard image={defaultImage} onUnassign={mockOnUnassign} />);

      await user.click(screen.getByTitle("Unassign from device"));
      expect(mockOnUnassign).toHaveBeenCalled();
    });

    it("shows delete button when handler provided", () => {
      render(<ImageCard image={defaultImage} onDelete={mockOnDelete} />);
      const deleteButton = screen.getByTitle("Delete image");
      expect(deleteButton).toBeInTheDocument();
    });

    it("shows confirmation before delete", async () => {
      const user = userEvent.setup();
      render(<ImageCard image={defaultImage} onDelete={mockOnDelete} />);

      await user.click(screen.getByTitle("Delete image"));
      expect(mockConfirm).toHaveBeenCalled();
    });

    it("calls onDelete when confirmation is accepted", async () => {
      const user = userEvent.setup();
      mockConfirm.mockReturnValue(true);
      render(<ImageCard image={defaultImage} onDelete={mockOnDelete} />);

      await user.click(screen.getByTitle("Delete image"));
      expect(mockOnDelete).toHaveBeenCalled();
    });

    it("does not call onDelete when confirmation is cancelled", async () => {
      const user = userEvent.setup();
      mockConfirm.mockReturnValue(false);
      render(<ImageCard image={defaultImage} onDelete={mockOnDelete} />);

      await user.click(screen.getByTitle("Delete image"));
      expect(mockOnDelete).not.toHaveBeenCalled();
    });
  });

  describe("sync button", () => {
    it("shows sync button for docker images when showSyncStatus is true", () => {
      render(<ImageCard image={defaultImage} showSyncStatus />);
      const syncButton = screen.getByTitle("Sync to all agents");
      expect(syncButton).toBeInTheDocument();
    });

    it("shows sync button for non-docker images when showSyncStatus is true", () => {
      const qcow2Image = { ...defaultImage, kind: "qcow2" };
      render(<ImageCard image={qcow2Image} showSyncStatus />);
      expect(screen.getByTitle("Sync to all agents")).toBeInTheDocument();
    });

    it("does not show sync button when showSyncStatus is false", () => {
      render(<ImageCard image={defaultImage} showSyncStatus={false} />);
      expect(screen.queryByTitle("Sync to all agents")).not.toBeInTheDocument();
    });

    it("calls API to sync image when sync button clicked", async () => {
      const user = userEvent.setup();
      render(<ImageCard image={defaultImage} showSyncStatus onSync={mockOnSync} />);

      await user.click(screen.getByTitle("Sync to all agents"));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          "/images/library/img-123/push",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({}),
          })
        );
      });
    });

    it("calls onSync callback after successful sync", async () => {
      const user = userEvent.setup();
      mockApiRequest.mockResolvedValue({});
      render(<ImageCard image={defaultImage} showSyncStatus onSync={mockOnSync} />);

      await user.click(screen.getByTitle("Sync to all agents"));

      await waitFor(() => {
        expect(mockOnSync).toHaveBeenCalled();
      });
    });

    it("shows alert on sync failure", async () => {
      const user = userEvent.setup();
      mockApiRequest.mockRejectedValue(new Error("Sync failed"));
      render(<ImageCard image={defaultImage} showSyncStatus />);

      await user.click(screen.getByTitle("Sync to all agents"));

      await waitFor(() => {
        expect(mockAlert).toHaveBeenCalledWith("Sync failed");
      });
    });

    it("disables sync button while syncing", async () => {
      const user = userEvent.setup();
      let resolveSync: () => void;
      const syncPromise = new Promise<void>((resolve) => {
        resolveSync = resolve;
      });
      mockApiRequest.mockReturnValue(syncPromise);

      render(<ImageCard image={defaultImage} showSyncStatus />);
      const syncButton = screen.getByTitle("Sync to all agents");

      await user.click(syncButton);

      // Check that button shows spinning icon
      const spinIcon = syncButton.querySelector(".fa-spin");
      expect(spinIcon).toBeInTheDocument();

      // Resolve to cleanup
      resolveSync!();
    });
  });

  describe("optional metadata", () => {
    it("handles missing size_bytes", () => {
      const imageWithoutSize = { ...defaultImage, size_bytes: null };
      render(<ImageCard image={imageWithoutSize} />);
      // Tooltip should not contain size info
      const card = screen.getByText("ceos-4.28.0F.tar").closest("[title]");
      expect(card?.getAttribute("title")).not.toContain("Size:");
    });

    it("handles missing vendor", () => {
      const imageWithoutVendor = { ...defaultImage, vendor: null };
      render(<ImageCard image={imageWithoutVendor} />);
      // Tooltip should not contain vendor info
      const card = screen.getByText("ceos-4.28.0F.tar").closest("[title]");
      expect(card?.getAttribute("title")).not.toContain("Vendor:");
    });

    it("handles missing version", () => {
      const imageWithoutVersion = { ...defaultImage, version: null };
      render(<ImageCard image={imageWithoutVersion} />);
      // Version should not appear inline
      expect(screen.queryByText("4.28.0F")).not.toBeInTheDocument();
    });

    it("handles missing uploaded_at", () => {
      const imageWithoutDate = { ...defaultImage, uploaded_at: null };
      render(<ImageCard image={imageWithoutDate} />);
      expect(screen.queryByText("Jan 15, 2024")).not.toBeInTheDocument();
    });

    it("handles missing notes", () => {
      const imageWithoutNotes = { ...defaultImage, notes: undefined };
      render(<ImageCard image={imageWithoutNotes} />);
      // Tooltip should not contain notes
      const card = screen.getByText("ceos-4.28.0F.tar").closest("[title]");
      expect(card?.getAttribute("title")).not.toContain("Notes:");
    });
  });

  describe("event propagation", () => {
    it("stops propagation on set default click", async () => {
      const user = userEvent.setup();
      const assignedImage = { ...defaultImage, device_id: "ceos", is_default: false };
      const mockContainerClick = vi.fn();

      render(
        <div onClick={mockContainerClick}>
          <ImageCard image={assignedImage} onSetDefault={mockOnSetDefault} />
        </div>
      );

      await user.click(screen.getByTitle("Set as default"));
      expect(mockOnSetDefault).toHaveBeenCalled();
      // Note: stopPropagation is called, so container click should not fire
    });

    it("stops propagation on unassign click", async () => {
      const user = userEvent.setup();
      const mockContainerClick = vi.fn();

      render(
        <div onClick={mockContainerClick}>
          <ImageCard image={defaultImage} onUnassign={mockOnUnassign} />
        </div>
      );

      await user.click(screen.getByTitle("Unassign from device"));
      expect(mockOnUnassign).toHaveBeenCalled();
    });

    it("stops propagation on delete click", async () => {
      const user = userEvent.setup();
      const mockContainerClick = vi.fn();

      render(
        <div onClick={mockContainerClick}>
          <ImageCard image={defaultImage} onDelete={mockOnDelete} />
        </div>
      );

      await user.click(screen.getByTitle("Delete image"));
      expect(mockConfirm).toHaveBeenCalled();
    });

    it("stops propagation on sync click", async () => {
      const user = userEvent.setup();
      const mockContainerClick = vi.fn();

      render(
        <div onClick={mockContainerClick}>
          <ImageCard image={defaultImage} showSyncStatus onSync={mockOnSync} />
        </div>
      );

      await user.click(screen.getByTitle("Sync to all agents"));
      // Event propagation should be stopped
    });
  });
});
