import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import JobLogModal from "./JobLogModal";

// Mock DetailPopup component
vi.mock("./DetailPopup", () => ({
  default: ({
    isOpen,
    onClose,
    title,
    children,
    width,
  }: {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
    width?: string;
  }) =>
    isOpen ? (
      <div data-testid="detail-popup" data-width={width}>
        <h2>{title}</h2>
        <button onClick={onClose} data-testid="close-popup">
          Close
        </button>
        {children}
      </div>
    ) : null,
}));

// Mock clipboard API
const mockWriteText = vi.fn();
Object.assign(navigator, {
  clipboard: {
    writeText: mockWriteText,
  },
});

describe("JobLogModal", () => {
  let mockStudioRequest: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    mockWriteText.mockResolvedValue(undefined);
    mockStudioRequest = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const createProps = (overrides = {}) => ({
    isOpen: true,
    onClose: vi.fn(),
    labId: "lab-123",
    jobId: "job-456",
    studioRequest: mockStudioRequest,
    ...overrides,
  });

  describe("rendering", () => {
    it("does not render when isOpen is false", () => {
      render(<JobLogModal {...createProps({ isOpen: false })} />);
      expect(screen.queryByTestId("detail-popup")).not.toBeInTheDocument();
    });

    it("renders when isOpen is true", () => {
      mockStudioRequest.mockResolvedValueOnce({ log: "Test log content" });
      render(<JobLogModal {...createProps()} />);
      expect(screen.getByTestId("detail-popup")).toBeInTheDocument();
    });

    it("displays Job Log title", () => {
      mockStudioRequest.mockResolvedValueOnce({ log: "Test log content" });
      render(<JobLogModal {...createProps()} />);
      expect(screen.getByText("Job Log")).toBeInTheDocument();
    });

    it("uses max-w-4xl width", () => {
      mockStudioRequest.mockResolvedValueOnce({ log: "Test log content" });
      render(<JobLogModal {...createProps()} />);
      expect(screen.getByTestId("detail-popup")).toHaveAttribute("data-width", "max-w-4xl");
    });
  });

  describe("loading state", () => {
    it("shows loading spinner while fetching", async () => {
      let resolveRequest: (value: { log: string }) => void;
      const loadingPromise = new Promise<{ log: string }>((resolve) => {
        resolveRequest = resolve;
      });
      mockStudioRequest.mockReturnValueOnce(loadingPromise);

      render(<JobLogModal {...createProps()} />);

      // Should show spinner
      const spinner = document.querySelector(".fa-spinner");
      expect(spinner).toBeInTheDocument();

      // Resolve the request
      resolveRequest!({ log: "Log content" });
      await waitFor(() => {
        expect(document.querySelector(".fa-spinner")).not.toBeInTheDocument();
      });
    });
  });

  describe("log content display", () => {
    it("displays log content when loaded", async () => {
      const logContent = "This is the job log content\nLine 2\nLine 3";
      mockStudioRequest.mockResolvedValueOnce({ log: logContent });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(screen.getByText(/This is the job log content/)).toBeInTheDocument();
      });
    });

    it("handles empty log content", async () => {
      mockStudioRequest.mockResolvedValueOnce({ log: "" });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(screen.getByText("No log content available.")).toBeInTheDocument();
      });
    });

    it("handles null log content", async () => {
      mockStudioRequest.mockResolvedValueOnce({ log: null });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(screen.getByText("No log content available.")).toBeInTheDocument();
      });
    });
  });

  describe("error handling", () => {
    it("displays error message on fetch failure", async () => {
      mockStudioRequest.mockRejectedValueOnce(new Error("Network error"));

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument();
      });
    });

    it("displays generic error message for non-Error objects", async () => {
      mockStudioRequest.mockRejectedValueOnce("Unknown error");

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(screen.getByText("Failed to load job log")).toBeInTheDocument();
      });
    });
  });

  describe("copy functionality", () => {
    it("shows copy button when log is loaded", async () => {
      mockStudioRequest.mockResolvedValueOnce({ log: "Test content" });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(screen.getByText("Copy")).toBeInTheDocument();
      });
    });

    it("invokes clipboard when copy button is clicked", async () => {
      const user = userEvent.setup();
      const logContent = "Log content to copy";
      mockStudioRequest.mockResolvedValueOnce({ log: logContent });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(screen.getByText("Copy")).toBeInTheDocument();
      });

      // Click the copy button
      const copyButton = screen.getByText("Copy");
      await user.click(copyButton);

      // The button text should change to "Copied!" after clicking
      await waitFor(() => {
        expect(screen.getByText("Copied!")).toBeInTheDocument();
      });
    });

    it("shows Copied! text after clicking copy", async () => {
      const user = userEvent.setup();
      mockStudioRequest.mockResolvedValueOnce({ log: "Test content" });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(screen.getByText("Copy")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Copy"));

      await waitFor(() => {
        expect(screen.getByText("Copied!")).toBeInTheDocument();
      });
    });
  });

  describe("API request", () => {
    it("fetches log with correct URL", async () => {
      mockStudioRequest.mockResolvedValueOnce({ log: "Test" });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith("/labs/lab-123/jobs/job-456/log");
      });
    });

    it("does not fetch when isOpen is false", () => {
      render(<JobLogModal {...createProps({ isOpen: false })} />);
      expect(mockStudioRequest).not.toHaveBeenCalled();
    });

    it("does not fetch when labId is empty", () => {
      render(<JobLogModal {...createProps({ labId: "" })} />);
      expect(mockStudioRequest).not.toHaveBeenCalled();
    });

    it("does not fetch when jobId is empty", () => {
      render(<JobLogModal {...createProps({ jobId: "" })} />);
      expect(mockStudioRequest).not.toHaveBeenCalled();
    });

    it("refetches when props change", async () => {
      mockStudioRequest.mockResolvedValue({ log: "Test" });

      const props = createProps();
      const { rerender } = render(<JobLogModal {...props} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledTimes(1);
      });

      rerender(<JobLogModal {...props} jobId="new-job-789" />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledTimes(2);
        expect(mockStudioRequest).toHaveBeenLastCalledWith(
          "/labs/lab-123/jobs/new-job-789/log"
        );
      });
    });
  });

  describe("close functionality", () => {
    it("calls onClose when close button is clicked", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      mockStudioRequest.mockResolvedValueOnce({ log: "Test" });

      render(<JobLogModal {...createProps({ onClose })} />);

      await user.click(screen.getByTestId("close-popup"));
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  describe("styling", () => {
    it("shows error icon on error", async () => {
      mockStudioRequest.mockRejectedValueOnce(new Error("Error"));

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        const errorIcon = document.querySelector(".fa-exclamation-circle");
        expect(errorIcon).toBeInTheDocument();
      });
    });

    it("shows file icon for empty log", async () => {
      mockStudioRequest.mockResolvedValueOnce({ log: "" });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        const fileIcon = document.querySelector(".fa-file-lines");
        expect(fileIcon).toBeInTheDocument();
      });
    });

    it("wraps log content in pre tag", async () => {
      mockStudioRequest.mockResolvedValueOnce({ log: "Test content" });

      render(<JobLogModal {...createProps()} />);

      await waitFor(() => {
        const preElement = document.querySelector("pre");
        expect(preElement).toBeInTheDocument();
        expect(preElement).toHaveClass("font-mono");
      });
    });
  });
});
