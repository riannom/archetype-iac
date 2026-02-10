import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SystemLogsModal from "./SystemLogsModal";

// Mock the API module
vi.mock("../../api", () => ({
  getSystemLogs: vi.fn(),
}));

import { getSystemLogs } from "../../api";
const mockGetSystemLogs = vi.mocked(getSystemLogs);

// Mock the Modal component
vi.mock("../../components/ui/Modal", () => ({
  Modal: ({
    isOpen,
    onClose,
    title,
    children,
  }: {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
  }) =>
    isOpen ? (
      <div data-testid="modal">
        <div data-testid="modal-title">{title}</div>
        <button data-testid="modal-close" onClick={onClose}>
          Close
        </button>
        {children}
      </div>
    ) : null,
}));

const mockLogEntries = [
  {
    timestamp: "2024-01-15T10:30:00Z",
    level: "INFO",
    service: "api",
    message: "Server started successfully",
    correlation_id: "abc12345-6789-0123-4567-890abcdef012",
  },
  {
    timestamp: "2024-01-15T10:31:00Z",
    level: "WARNING",
    service: "worker",
    message: "Job queue is getting full",
    correlation_id: null,
  },
  {
    timestamp: "2024-01-15T10:32:00Z",
    level: "ERROR",
    service: "agent",
    message: "Connection to container failed",
    correlation_id: "def12345-6789-0123-4567-890abcdef012",
  },
  {
    timestamp: "2024-01-15T10:33:00Z",
    level: "DEBUG",
    service: "api",
    message: "Processing request",
    correlation_id: null,
  },
  {
    timestamp: "2024-01-15T10:34:00Z",
    level: "WARN",
    service: "worker",
    message: "Deprecated method used",
    correlation_id: null,
  },
];

const mockLogResponse = {
  entries: mockLogEntries,
  total_count: 150,
  has_more: true,
};

describe("SystemLogsModal", () => {
  const mockOnClose = vi.fn();

  const defaultProps = {
    isOpen: true,
    onClose: mockOnClose,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockGetSystemLogs.mockResolvedValue(mockLogResponse);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe("Rendering", () => {
    it("does not render when isOpen is false", () => {
      render(<SystemLogsModal {...defaultProps} isOpen={false} />);

      expect(screen.queryByTestId("modal")).not.toBeInTheDocument();
    });

    it("renders when isOpen is true", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      expect(screen.getByTestId("modal")).toBeInTheDocument();
      expect(screen.getByTestId("modal-title")).toHaveTextContent("System Logs");
    });

    it("calls onClose when modal close is triggered", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<SystemLogsModal {...defaultProps} />);

      await user.click(screen.getByTestId("modal-close"));

      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe("Initial data fetch", () => {
    it("fetches logs when modal opens", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledWith({
          since: "1h",
          limit: 200,
        });
      });
    });

    it("does not fetch logs when modal is closed", () => {
      render(<SystemLogsModal {...defaultProps} isOpen={false} />);

      expect(mockGetSystemLogs).not.toHaveBeenCalled();
    });
  });

  // Helper functions for accessing filter controls
  const getServiceSelect = () => {
    const label = screen.getByText("Service:");
    return label.parentElement?.querySelector("select") as HTMLSelectElement;
  };

  const getLevelSelect = () => {
    const label = screen.getByText("Level:");
    return label.parentElement?.querySelector("select") as HTMLSelectElement;
  };

  const getTimeSelect = () => {
    const label = screen.getByText("Time:");
    return label.parentElement?.querySelector("select") as HTMLSelectElement;
  };

  describe("Filter controls", () => {
    it("renders service filter dropdown", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      expect(screen.getByText("Service:")).toBeInTheDocument();
      expect(getServiceSelect()).toBeInTheDocument();
    });

    it("renders level filter dropdown", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      expect(screen.getByText("Level:")).toBeInTheDocument();
      expect(getLevelSelect()).toBeInTheDocument();
    });

    it("renders time range filter dropdown", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      expect(screen.getByText("Time:")).toBeInTheDocument();
      expect(getTimeSelect()).toBeInTheDocument();
    });

    it("renders search input", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      expect(screen.getByPlaceholderText("Search logs...")).toBeInTheDocument();
    });

    it("renders auto-refresh checkbox", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      expect(screen.getByText("Auto-refresh")).toBeInTheDocument();
      expect(screen.getByRole("checkbox")).toBeChecked();
    });

  });

  describe("Service filter", () => {
    it("includes all service options", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      const serviceSelect = getServiceSelect();
      const options = serviceSelect.querySelectorAll("option");

      expect(options.length).toBe(4);
      // Check options by their text content
      const optionTexts = Array.from(options).map(o => o.textContent);
      expect(optionTexts).toContain("All");
      expect(optionTexts).toContain("api");
      expect(optionTexts).toContain("worker");
      expect(optionTexts).toContain("agent");
    });

    it("refetches logs when service filter changes", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalled();
      });

      mockGetSystemLogs.mockClear();

      const serviceSelect = getServiceSelect();
      await user.selectOptions(serviceSelect, "api");

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledWith(
          expect.objectContaining({
            service: "api",
          })
        );
      });
    });
  });

  describe("Level filter", () => {
    it("includes all level options", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      const levelSelect = getLevelSelect();
      const options = levelSelect.querySelectorAll("option");

      expect(options.length).toBe(4);
      const optionTexts = Array.from(options).map(o => o.textContent);
      expect(optionTexts).toContain("All");
      expect(optionTexts).toContain("INFO");
      expect(optionTexts).toContain("WARNING");
      expect(optionTexts).toContain("ERROR");
    });

    it("refetches logs when level filter changes", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalled();
      });

      mockGetSystemLogs.mockClear();

      const levelSelect = getLevelSelect();
      await user.selectOptions(levelSelect, "ERROR");

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledWith(
          expect.objectContaining({
            level: "ERROR",
          })
        );
      });
    });
  });

  describe("Time range filter", () => {
    it("refetches logs when time range changes", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalled();
      });

      mockGetSystemLogs.mockClear();

      const timeSelect = getTimeSelect();
      await user.selectOptions(timeSelect, "15m");

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledWith(
          expect.objectContaining({
            since: "15m",
          })
        );
      });
    });
  });

  describe("Search functionality", () => {
    it("triggers search on Enter key", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalled();
      });

      mockGetSystemLogs.mockClear();

      const searchInput = screen.getByPlaceholderText("Search logs...");
      await user.type(searchInput, "error");
      await user.keyboard("{Enter}");

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledWith(
          expect.objectContaining({
            search: "error",
          })
        );
      });
    });
  });

  describe("Auto-refresh functionality", () => {
    it("auto-refreshes every 5 seconds when enabled", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledTimes(1);
      });

      // Advance time by 5 seconds
      vi.advanceTimersByTime(5000);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledTimes(2);
      });

      // Advance time by another 5 seconds
      vi.advanceTimersByTime(5000);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledTimes(3);
      });
    });

    it("stops auto-refresh when checkbox is unchecked", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledTimes(1);
      });

      // Disable auto-refresh
      const checkbox = screen.getByRole("checkbox");
      await user.click(checkbox);

      expect(checkbox).not.toBeChecked();

      mockGetSystemLogs.mockClear();

      // Advance time by 10 seconds
      vi.advanceTimersByTime(10000);

      // Should not have fetched
      expect(mockGetSystemLogs).not.toHaveBeenCalled();
    });
  });

  describe("Logs table display", () => {
    it("renders table headers", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Time")).toBeInTheDocument();
        expect(screen.getByText("Level")).toBeInTheDocument();
        expect(screen.getByText("Service")).toBeInTheDocument();
        expect(screen.getByText("Message")).toBeInTheDocument();
      });
    });

    it("displays log entries", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Server started successfully")).toBeInTheDocument();
        expect(screen.getByText("Job queue is getting full")).toBeInTheDocument();
        expect(screen.getByText("Connection to container failed")).toBeInTheDocument();
      });
    });

    it("displays log levels with correct styling", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        // Get all log level badges (exclude the filter dropdown options)
        const infoBadges = screen.getAllByText("INFO");
        const warningBadges = screen.getAllByText("WARNING");
        const errorBadges = screen.getAllByText("ERROR");

        // Find the badge elements (span with styling classes, not option elements)
        const infoBadge = infoBadges.find(el => el.tagName === "SPAN" && el.classList.contains("text-green-600"));
        const warningBadge = warningBadges.find(el => el.tagName === "SPAN" && el.classList.contains("text-amber-600"));
        const errorBadge = errorBadges.find(el => el.tagName === "SPAN" && el.classList.contains("text-red-600"));

        expect(infoBadge).toBeTruthy();
        expect(warningBadge).toBeTruthy();
        expect(errorBadge).toBeTruthy();
      });
    });

    it("displays WARN level with amber styling", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        const warnBadge = screen.getByText("WARN");
        expect(warnBadge).toHaveClass("text-amber-600");
      });
    });

    it("displays DEBUG level with stone styling", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        const debugBadge = screen.getByText("DEBUG");
        expect(debugBadge).toHaveClass("text-stone-500");
      });
    });

    it("displays service names", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getAllByText("api").length).toBeGreaterThanOrEqual(2);
        expect(screen.getAllByText("worker").length).toBeGreaterThanOrEqual(2);
        expect(screen.getAllByText("agent").length).toBeGreaterThanOrEqual(1);
      });
    });

    it("displays correlation ID when present", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        // Shows first 8 characters of correlation ID
        expect(screen.getByText("abc12345")).toBeInTheDocument();
        expect(screen.getByText("def12345")).toBeInTheDocument();
      });
    });
  });

  describe("Empty state", () => {
    it("shows empty state when no logs found", async () => {
      mockGetSystemLogs.mockResolvedValue({
        entries: [],
        total_count: 0,
        has_more: false,
      });

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("No logs found")).toBeInTheDocument();
        expect(screen.getByText("Logs will appear here when available")).toBeInTheDocument();
      });
    });

    it("suggests adjusting filters when no logs found with filters", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      mockGetSystemLogs.mockResolvedValue({
        entries: [],
        total_count: 0,
        has_more: false,
      });

      render(<SystemLogsModal {...defaultProps} />);

      // Apply a filter
      const serviceSelect = getServiceSelect();
      await user.selectOptions(serviceSelect, "api");

      await waitFor(() => {
        expect(screen.getByText("Try adjusting your filters")).toBeInTheDocument();
      });
    });
  });

  describe("Error handling", () => {
    it("displays error message when fetch fails", async () => {
      mockGetSystemLogs.mockRejectedValue(new Error("Network error"));

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument();
      });
    });

    it("displays generic error for non-Error exceptions", async () => {
      mockGetSystemLogs.mockRejectedValue("Unknown error");

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Failed to fetch logs")).toBeInTheDocument();
      });
    });
  });

  describe("Footer", () => {
    it("displays entry count", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Showing 5 of 150 entries")).toBeInTheDocument();
      });
    });

    it("displays Grafana link", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        const grafanaLink = screen.getByText("Open in Grafana");
        expect(grafanaLink).toBeInTheDocument();
        expect(grafanaLink.closest("a")).toHaveAttribute("target", "_blank");
        expect(grafanaLink.closest("a")).toHaveAttribute("rel", "noopener noreferrer");
      });
    });

    it("constructs correct Grafana URL with time range", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        const grafanaLink = screen.getByText("Open in Grafana").closest("a");
        expect(grafanaLink?.getAttribute("href")).toContain("now-1h");
      });
    });
  });

  describe("Time formatting", () => {
    it("formats time correctly", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        // The exact format depends on locale, but we can check that times are displayed
        // Looking for time patterns like "10:30:00"
        const timeElements = document.querySelectorAll("td");
        const hasTime = Array.from(timeElements).some((el) =>
          el.textContent?.match(/\d{2}:\d{2}:\d{2}/)
        );
        expect(hasTime).toBe(true);
      });
    });

    it("formats date correctly", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        // Looking for date patterns like "Jan 15"
        const dateElements = document.querySelectorAll("td");
        const hasDate = Array.from(dateElements).some((el) =>
          el.textContent?.match(/Jan\s+\d+/)
        );
        expect(hasDate).toBe(true);
      });
    });
  });

  describe("Filter combinations", () => {
    it("sends all filters when multiple are selected", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

      render(<SystemLogsModal {...defaultProps} />);

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalled();
      });

      mockGetSystemLogs.mockClear();

      // Set service filter
      const serviceSelect = getServiceSelect();
      await user.selectOptions(serviceSelect, "api");

      // Set level filter
      const levelSelect = getLevelSelect();
      await user.selectOptions(levelSelect, "ERROR");

      // Set time range
      const timeSelect = getTimeSelect();
      await user.selectOptions(timeSelect, "24h");

      // Type search and press enter
      const searchInput = screen.getByPlaceholderText("Search logs...");
      await user.type(searchInput, "connection");
      await user.keyboard("{Enter}");

      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledWith({
          service: "api",
          level: "ERROR",
          since: "24h",
          search: "connection",
          limit: 200,
        });
      });
    });

    it("does not send filter params when set to All", async () => {
      render(<SystemLogsModal {...defaultProps} />);

      // Initial fetch uses default filters (no service or level)
      await waitFor(() => {
        expect(mockGetSystemLogs).toHaveBeenCalledWith({
          since: "1h",
          limit: 200,
        });
      });

      // Verify no service or level params
      const callArgs = mockGetSystemLogs.mock.calls[0][0];
      expect(callArgs.service).toBeUndefined();
      expect(callArgs.level).toBeUndefined();
    });
  });
});
