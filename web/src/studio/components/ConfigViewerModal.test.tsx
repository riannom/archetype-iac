import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConfigViewerModal from "./ConfigViewerModal";

// Mock DetailPopup component
vi.mock("./DetailPopup", () => ({
  default: ({
    isOpen,
    onClose,
    title,
    children,
  }: {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
  }) => {
    if (!isOpen) return null;
    return (
      <div data-testid="detail-popup">
        <div data-testid="popup-title">{title}</div>
        <button data-testid="close-button" onClick={onClose}>
          Close
        </button>
        <div data-testid="popup-content">{children}</div>
      </div>
    );
  },
}));

// Mock navigator.clipboard - using Object.assign pattern from other tests
const mockClipboard = {
  writeText: vi.fn().mockResolvedValue(undefined),
};
Object.assign(navigator, { clipboard: mockClipboard });

interface SavedConfig {
  node_name: string;
  config: string;
  last_modified: number;
  exists: boolean;
}

const createMockConfig = (
  overrides: Partial<SavedConfig> = {}
): SavedConfig => ({
  node_name: overrides.node_name || "router1",
  config: overrides.config || "! Configuration\nhostname router1",
  last_modified: overrides.last_modified || 1705312200,
  exists: overrides.exists ?? true,
});

describe("ConfigViewerModal", () => {
  const mockStudioRequest = vi.fn();
  const mockOnClose = vi.fn();

  const defaultProps = {
    isOpen: true,
    onClose: mockOnClose,
    labId: "test-lab-123",
    studioRequest: mockStudioRequest,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockClipboard.writeText.mockClear();
    mockClipboard.writeText.mockResolvedValue(undefined);
    mockStudioRequest.mockResolvedValue({ configs: [] });
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe("Modal visibility", () => {
    it("renders nothing when isOpen is false", () => {
      render(<ConfigViewerModal {...defaultProps} isOpen={false} />);

      expect(screen.queryByTestId("detail-popup")).not.toBeInTheDocument();
    });

    it("renders modal when isOpen is true", () => {
      render(<ConfigViewerModal {...defaultProps} />);

      expect(screen.getByTestId("detail-popup")).toBeInTheDocument();
    });

    it("displays correct title for all configs view", () => {
      render(<ConfigViewerModal {...defaultProps} />);

      expect(screen.getByTestId("popup-title")).toHaveTextContent(
        "Saved Configurations"
      );
    });

    it("displays correct title for single node config", () => {
      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="Router1" />
      );

      expect(screen.getByTestId("popup-title")).toHaveTextContent(
        "Config: Router1"
      );
    });

    it("calls onClose when close button is clicked", async () => {
      vi.useRealTimers();
      const user = userEvent.setup();

      render(<ConfigViewerModal {...defaultProps} />);

      await user.click(screen.getByTestId("close-button"));

      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe("Loading state", () => {
    it("shows loading spinner while loading", async () => {
      mockStudioRequest.mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(() => resolve({ configs: [] }), 100)
          )
      );

      render(<ConfigViewerModal {...defaultProps} />);

      expect(document.querySelector(".fa-spinner")).toBeInTheDocument();
    });

    it("calls studioRequest for all configs when no nodeName", async () => {
      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/configs"
        );
      });
    });

    it("calls studioRequest for single node config when nodeName provided", async () => {
      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="Router1" />
      );

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/configs/Router1"
        );
      });
    });

    it("encodes nodeName in URL", async () => {
      render(
        <ConfigViewerModal
          {...defaultProps}
          nodeId="node-1"
          nodeName="Router/One"
        />
      );

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/configs/Router%2FOne"
        );
      });
    });

    it("does not fetch when labId is empty", async () => {
      render(<ConfigViewerModal {...defaultProps} labId="" />);

      await waitFor(() => {
        expect(mockStudioRequest).not.toHaveBeenCalled();
      });
    });
  });

  describe("Error state", () => {
    it("shows error message when loading fails", async () => {
      mockStudioRequest.mockRejectedValue(new Error("Network error"));

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument();
      });
    });

    it("shows error icon when loading fails", async () => {
      mockStudioRequest.mockRejectedValue(new Error("Failed to load"));

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(
          document.querySelector(".fa-exclamation-circle")
        ).toBeInTheDocument();
      });
    });

    it("shows generic error message for non-Error objects", async () => {
      mockStudioRequest.mockRejectedValue("Unknown error");

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Failed to load configs")).toBeInTheDocument();
      });
    });

    it("clears configs when error occurs", async () => {
      const configs = [createMockConfig()];
      mockStudioRequest
        .mockResolvedValueOnce({ configs })
        .mockRejectedValueOnce(new Error("Network error"));

      const { rerender } = render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/router1/i)).toBeInTheDocument();
      });

      // Re-render to trigger refetch (simulate labId change)
      rerender(<ConfigViewerModal {...defaultProps} labId="other-lab" />);

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument();
      });
    });
  });

  describe("Empty state", () => {
    it("shows empty state when no configs found", async () => {
      mockStudioRequest.mockResolvedValue({ configs: [] });

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(
          screen.getByText("No saved configurations found.")
        ).toBeInTheDocument();
      });
    });

    it("shows instruction to extract configs", async () => {
      mockStudioRequest.mockResolvedValue({ configs: [] });

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(
          screen.getByText(/Run "Extract Configs" from Runtime Control/)
        ).toBeInTheDocument();
      });
    });

    it("shows file icon in empty state", async () => {
      mockStudioRequest.mockResolvedValue({ configs: [] });

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(document.querySelector(".fa-file-code")).toBeInTheDocument();
      });
    });
  });

  describe("Single config display", () => {
    it("displays config content", async () => {
      const config = createMockConfig({
        config: "hostname router1\ninterface eth0",
      });
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(screen.getByText(/hostname router1/)).toBeInTheDocument();
        expect(screen.getByText(/interface eth0/)).toBeInTheDocument();
      });
    });

    it("does not show tabs for single config", async () => {
      const config = createMockConfig();
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        // Should not have tab buttons for single config
        const tabButtons = document.querySelectorAll(
          'button[class*="border-b-2"]'
        );
        expect(tabButtons.length).toBe(0);
      });
    });

    it("sets active tab to node name for single config", async () => {
      const config = createMockConfig({ node_name: "router1" });
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(screen.getByText(/hostname router1/)).toBeInTheDocument();
      });
    });
  });

  describe("Multiple configs with tabs", () => {
    it("displays tabs for multiple configs", async () => {
      const configs = [
        createMockConfig({ node_name: "router1" }),
        createMockConfig({ node_name: "switch1" }),
      ];
      mockStudioRequest.mockResolvedValue({ configs });

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("router1")).toBeInTheDocument();
        expect(screen.getByText("switch1")).toBeInTheDocument();
      });
    });

    it("first config is active by default", async () => {
      const configs = [
        createMockConfig({
          node_name: "router1",
          config: "config for router1",
        }),
        createMockConfig({
          node_name: "switch1",
          config: "config for switch1",
        }),
      ];
      mockStudioRequest.mockResolvedValue({ configs });

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("config for router1")).toBeInTheDocument();
        expect(
          screen.queryByText("config for switch1")
        ).not.toBeInTheDocument();
      });
    });

    it("switches config when tab is clicked", async () => {
      vi.useRealTimers();
      const user = userEvent.setup();
      const configs = [
        createMockConfig({
          node_name: "router1",
          config: "config for router1",
        }),
        createMockConfig({
          node_name: "switch1",
          config: "config for switch1",
        }),
      ];
      mockStudioRequest.mockResolvedValue({ configs });

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("switch1")).toBeInTheDocument();
      });

      await user.click(screen.getByText("switch1"));

      await waitFor(() => {
        expect(screen.getByText("config for switch1")).toBeInTheDocument();
      });
    });

    it("applies active styling to selected tab", async () => {
      const configs = [
        createMockConfig({ node_name: "router1" }),
        createMockConfig({ node_name: "switch1" }),
      ];
      mockStudioRequest.mockResolvedValue({ configs });

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        const activeTab = screen.getByText("router1").closest("button");
        expect(activeTab).toHaveClass("text-sage-600");
      });
    });
  });

  describe("Config metadata", () => {
    it("displays last modified timestamp", async () => {
      const config = createMockConfig({
        last_modified: 1705312200, // Unix timestamp
      });
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(screen.getByText(/Last modified:/)).toBeInTheDocument();
      });
    });

    it("shows clock icon next to timestamp", async () => {
      const config = createMockConfig();
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(document.querySelector(".fa-clock")).toBeInTheDocument();
      });
    });
  });

  describe("Copy functionality", () => {
    it("displays copy button", async () => {
      const config = createMockConfig();
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(screen.getByText("Copy")).toBeInTheDocument();
      });
    });

    it("calls handleCopy which updates state when copy button clicked", async () => {
      vi.useRealTimers();
      const user = userEvent.setup();
      const config = createMockConfig({ node_name: "router1", config: "test config content" });
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      // Wait for config content to be displayed
      await waitFor(() => {
        expect(screen.getByText("test config content")).toBeInTheDocument();
      });

      const copyButton = screen.getByText("Copy").closest("button");
      await user.click(copyButton!);

      // Wait for "Copied!" text to appear which confirms the handler ran
      // and the clipboard writeText call completed (even if async)
      await waitFor(() => {
        expect(screen.getByText("Copied!")).toBeInTheDocument();
      });
    });

    it("shows 'Copied!' after successful copy", async () => {
      vi.useRealTimers();
      const user = userEvent.setup();
      const config = createMockConfig();
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(screen.getByText("Copy")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Copy"));

      await waitFor(() => {
        expect(screen.getByText("Copied!")).toBeInTheDocument();
      });
    });

    it("reverts copy button text after timeout", async () => {
      vi.useRealTimers();
      const user = userEvent.setup();
      const config = createMockConfig();
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(screen.getByText("Copy")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Copy"));

      await waitFor(() => {
        expect(screen.getByText("Copied!")).toBeInTheDocument();
      });

      // Wait for timeout
      await waitFor(
        () => {
          expect(screen.getByText("Copy")).toBeInTheDocument();
        },
        { timeout: 3000 }
      );
    });

    it("shows check icon after copy", async () => {
      vi.useRealTimers();
      const user = userEvent.setup();
      const config = createMockConfig();
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(screen.getByText("Copy")).toBeInTheDocument();
      });

      // Initially shows copy icon
      expect(document.querySelector(".fa-copy")).toBeInTheDocument();

      await user.click(screen.getByText("Copy"));

      await waitFor(() => {
        expect(document.querySelector(".fa-check")).toBeInTheDocument();
      });
    });

    it("triggers copy handler when clicking copy on second tab", async () => {
      vi.useRealTimers();
      const user = userEvent.setup();
      const configs = [
        createMockConfig({ node_name: "router1", config: "router1 config" }),
        createMockConfig({ node_name: "switch1", config: "switch1 config" }),
      ];
      mockStudioRequest.mockResolvedValue({ configs });

      render(<ConfigViewerModal {...defaultProps} />);

      // Switch to second tab
      await waitFor(() => {
        expect(screen.getByText("switch1")).toBeInTheDocument();
      });

      await user.click(screen.getByText("switch1"));

      await waitFor(() => {
        expect(screen.getByText("switch1 config")).toBeInTheDocument();
      });

      // Copy
      const copyButton = screen.getByText("Copy").closest("button");
      await user.click(copyButton!);

      // Wait for "Copied!" text to appear which confirms the handler ran
      await waitFor(() => {
        expect(screen.getByText("Copied!")).toBeInTheDocument();
      });
    });
  });

  describe("Config rendering", () => {
    it("renders config in monospace font", async () => {
      const config = createMockConfig();
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        const pre = document.querySelector("pre");
        expect(pre).toBeInTheDocument();
        expect(pre).toHaveClass("font-mono");
      });
    });

    it("preserves whitespace in config", async () => {
      const config = createMockConfig({
        config: "  indented\n    more indent",
      });
      mockStudioRequest.mockResolvedValue(config);

      render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        const pre = document.querySelector("pre");
        expect(pre).toHaveClass("whitespace-pre");
      });
    });
  });

  describe("Refetching behavior", () => {
    it("refetches when labId changes", async () => {
      const { rerender } = render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/configs"
        );
      });

      mockStudioRequest.mockClear();

      rerender(<ConfigViewerModal {...defaultProps} labId="new-lab-456" />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/new-lab-456/configs"
        );
      });
    });

    it("refetches when nodeName changes", async () => {
      const config = createMockConfig();
      mockStudioRequest.mockResolvedValue(config);

      const { rerender } = render(
        <ConfigViewerModal {...defaultProps} nodeId="node-1" nodeName="router1" />
      );

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/configs/router1"
        );
      });

      mockStudioRequest.mockClear();

      rerender(
        <ConfigViewerModal {...defaultProps} nodeId="node-2" nodeName="switch1" />
      );

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/configs/switch1"
        );
      });
    });

    it("refetches when modal opens", async () => {
      const { rerender } = render(
        <ConfigViewerModal {...defaultProps} isOpen={false} />
      );

      expect(mockStudioRequest).not.toHaveBeenCalled();

      rerender(<ConfigViewerModal {...defaultProps} isOpen={true} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalled();
      });
    });
  });

  describe("Edge cases", () => {
    it("handles null configs array", async () => {
      mockStudioRequest.mockResolvedValue({ configs: null });

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(
          screen.getByText("No saved configurations found.")
        ).toBeInTheDocument();
      });
    });

    it("handles undefined configs property", async () => {
      mockStudioRequest.mockResolvedValue({});

      render(<ConfigViewerModal {...defaultProps} />);

      await waitFor(() => {
        expect(
          screen.getByText("No saved configurations found.")
        ).toBeInTheDocument();
      });
    });
  });
});
