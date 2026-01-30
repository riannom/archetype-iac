import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImageSyncProgress from "./ImageSyncProgress";

// Mock the api module
vi.mock("../api", () => ({
  apiRequest: vi.fn(),
}));

// Import the mocked apiRequest after mocking
import { apiRequest } from "../api";
const mockApiRequest = vi.mocked(apiRequest);

interface SyncJob {
  id: string;
  image_id: string;
  host_id: string;
  host_name: string | null;
  status: string;
  progress_percent: number;
  bytes_transferred: number;
  total_bytes: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

describe("ImageSyncProgress", () => {
  const createSyncJob = (overrides: Partial<SyncJob> = {}): SyncJob => ({
    id: "job-123",
    image_id: "img-456",
    host_id: "host-789",
    host_name: "Test Host",
    status: "pending",
    progress_percent: 0,
    bytes_transferred: 0,
    total_bytes: 1073741824, // 1GB
    error_message: null,
    started_at: null,
    completed_at: null,
    created_at: "2024-01-15T10:00:00Z",
    ...overrides,
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe("loading state", () => {
    it("shows loading spinner initially", async () => {
      mockApiRequest.mockReturnValue(new Promise(() => {})); // Never resolves
      render(<ImageSyncProgress />);

      expect(screen.getByText("Loading sync jobs...")).toBeInTheDocument();
      const spinner = document.querySelector(".fa-spinner.fa-spin");
      expect(spinner).toBeInTheDocument();
    });
  });

  describe("error state", () => {
    it("shows error message when API fails", async () => {
      mockApiRequest.mockRejectedValue(new Error("API Error"));
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("API Error")).toBeInTheDocument();
      });

      const errorIcon = document.querySelector(".fa-exclamation-triangle");
      expect(errorIcon).toBeInTheDocument();
    });

    it("shows generic error for non-Error objects", async () => {
      mockApiRequest.mockRejectedValue("Something went wrong");
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("Failed to fetch sync jobs")).toBeInTheDocument();
      });
    });
  });

  describe("empty state", () => {
    it("shows empty message when no jobs found", async () => {
      mockApiRequest.mockResolvedValue([]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("No sync jobs found")).toBeInTheDocument();
      });
    });
  });

  describe("job display", () => {
    it("displays job status badge", async () => {
      const job = createSyncJob({ status: "pending" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("pending")).toBeInTheDocument();
      });
    });

    it("displays host name when available", async () => {
      const job = createSyncJob({ host_name: "Production Agent" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("Production Agent")).toBeInTheDocument();
      });
    });

    it("displays truncated host_id when host_name is null", async () => {
      const job = createSyncJob({
        host_name: null,
        host_id: "abcdefgh-1234-5678-9012-ijklmnopqrst",
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("abcdefgh")).toBeInTheDocument();
      });
    });

    it("displays image_id", async () => {
      const job = createSyncJob({ image_id: "ceos:4.28.0F" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("ceos:4.28.0F")).toBeInTheDocument();
      });
    });

    it("displays truncated job id in footer", async () => {
      const job = createSyncJob({ id: "abcdefgh-1234-5678-9012" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("abcdefgh")).toBeInTheDocument();
      });
    });
  });

  describe("status colors and icons", () => {
    const testCases = [
      { status: "pending", icon: "fa-clock" },
      { status: "transferring", icon: "fa-arrow-right-arrow-left" },
      { status: "loading", icon: "fa-spinner" },
      { status: "completed", icon: "fa-check" },
      { status: "failed", icon: "fa-xmark" },
      { status: "cancelled", icon: "fa-ban" },
    ];

    testCases.forEach(({ status, icon }) => {
      it(`shows correct icon for ${status} status`, async () => {
        const job = createSyncJob({ status });
        mockApiRequest.mockResolvedValue([job]);
        render(<ImageSyncProgress />);

        await waitFor(() => {
          const iconElement = document.querySelector(`.${icon}`);
          expect(iconElement).toBeInTheDocument();
        });
      });
    });
  });

  describe("progress bar", () => {
    it("shows progress bar for transferring status", async () => {
      const job = createSyncJob({
        status: "transferring",
        progress_percent: 50,
        bytes_transferred: 536870912, // 512MB
        total_bytes: 1073741824, // 1GB
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("50%")).toBeInTheDocument();
        // parseFloat strips trailing zeros: "512 MB" and "1 GB"
        expect(screen.getByText("512 MB / 1 GB")).toBeInTheDocument();
      });
    });

    it("shows progress bar for loading status", async () => {
      const job = createSyncJob({
        status: "loading",
        progress_percent: 75,
        bytes_transferred: 805306368, // 768MB
        total_bytes: 1073741824, // 1GB
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("75%")).toBeInTheDocument();
      });
    });

    it("does not show progress bar for pending status", async () => {
      const job = createSyncJob({ status: "pending", progress_percent: 0 });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("pending")).toBeInTheDocument();
      });

      expect(screen.queryByText("0%")).not.toBeInTheDocument();
    });

    it("does not show progress bar for completed status", async () => {
      const job = createSyncJob({ status: "completed", progress_percent: 100 });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("completed")).toBeInTheDocument();
      });

      expect(screen.queryByText("100%")).not.toBeInTheDocument();
    });

    it("handles zero total_bytes gracefully", async () => {
      const job = createSyncJob({
        status: "transferring",
        progress_percent: 0,
        bytes_transferred: 0,
        total_bytes: 0,
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("0%")).toBeInTheDocument();
        expect(screen.getByText("0 B / 0 B")).toBeInTheDocument();
      });
    });
  });

  describe("error message display", () => {
    it("shows error message for failed jobs", async () => {
      const job = createSyncJob({
        status: "failed",
        error_message: "Connection timeout",
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("Connection timeout")).toBeInTheDocument();
      });

      const errorIcon = document.querySelector(".fa-exclamation-circle");
      expect(errorIcon).toBeInTheDocument();
    });

    it("does not show error section when no error_message", async () => {
      const job = createSyncJob({ status: "failed", error_message: null });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("failed")).toBeInTheDocument();
      });

      const errorCircle = document.querySelector(".fa-exclamation-circle");
      expect(errorCircle).not.toBeInTheDocument();
    });
  });

  describe("duration display", () => {
    it("shows duration for active jobs", async () => {
      const now = new Date("2024-01-15T10:05:30Z");
      vi.setSystemTime(now);

      const job = createSyncJob({
        status: "transferring",
        started_at: "2024-01-15T10:00:00Z",
        completed_at: null,
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("5m 30s")).toBeInTheDocument();
      });
    });

    it("shows duration for completed jobs", async () => {
      const job = createSyncJob({
        status: "completed",
        started_at: "2024-01-15T10:00:00Z",
        completed_at: "2024-01-15T10:02:30Z",
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("2m 30s")).toBeInTheDocument();
      });
    });

    it("shows dash when started_at is null", async () => {
      const job = createSyncJob({
        status: "pending",
        started_at: null,
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("-")).toBeInTheDocument();
      });
    });

    it("shows seconds format for short durations", async () => {
      const job = createSyncJob({
        status: "completed",
        started_at: "2024-01-15T10:00:00Z",
        completed_at: "2024-01-15T10:00:45Z",
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("45s")).toBeInTheDocument();
      });
    });
  });

  describe("cancel functionality", () => {
    it("shows cancel button for pending jobs", async () => {
      const job = createSyncJob({ status: "pending" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("Cancel")).toBeInTheDocument();
      });
    });

    it("shows cancel button for transferring jobs", async () => {
      const job = createSyncJob({ status: "transferring" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("Cancel")).toBeInTheDocument();
      });
    });

    it("shows cancel button for loading jobs", async () => {
      const job = createSyncJob({ status: "loading" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("Cancel")).toBeInTheDocument();
      });
    });

    it("does not show cancel button for completed jobs", async () => {
      const job = createSyncJob({ status: "completed" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("completed")).toBeInTheDocument();
      });

      expect(screen.queryByText("Cancel")).not.toBeInTheDocument();
    });

    it("does not show cancel button for failed jobs", async () => {
      const job = createSyncJob({ status: "failed" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("failed")).toBeInTheDocument();
      });

      expect(screen.queryByText("Cancel")).not.toBeInTheDocument();
    });

    it("calls API to cancel job when cancel is clicked", async () => {
      vi.useRealTimers();
      const user = userEvent.setup();
      const job = createSyncJob({ id: "job-to-cancel", status: "pending" });
      mockApiRequest.mockResolvedValue([job]);

      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("Cancel")).toBeInTheDocument();
      });

      mockApiRequest.mockClear();
      mockApiRequest.mockResolvedValue([]);

      await user.click(screen.getByText("Cancel"));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          "/images/sync-jobs/job-to-cancel",
          { method: "DELETE" }
        );
      });

      vi.useFakeTimers({ shouldAdvanceTime: true });
    });
  });

  describe("filtering", () => {
    it("filters by imageId when provided", async () => {
      mockApiRequest.mockResolvedValue([]);
      render(<ImageSyncProgress imageId="specific-image" />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          expect.stringContaining("image_id=specific-image")
        );
      });
    });

    it("filters by hostId when provided", async () => {
      mockApiRequest.mockResolvedValue([]);
      render(<ImageSyncProgress hostId="specific-host" />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          expect.stringContaining("host_id=specific-host")
        );
      });
    });

    it("applies limit parameter", async () => {
      mockApiRequest.mockResolvedValue([]);
      render(<ImageSyncProgress maxJobs={5} />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          expect.stringContaining("limit=5")
        );
      });
    });

    it("uses default limit of 10", async () => {
      mockApiRequest.mockResolvedValue([]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith(
          expect.stringContaining("limit=10")
        );
      });
    });
  });

  describe("showCompleted filtering", () => {
    it("includes completed jobs by default", async () => {
      const jobs = [
        createSyncJob({ id: "1", status: "completed" }),
        createSyncJob({ id: "2", status: "pending" }),
      ];
      mockApiRequest.mockResolvedValue(jobs);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("completed")).toBeInTheDocument();
        expect(screen.getByText("pending")).toBeInTheDocument();
      });
    });

    it("filters out completed jobs when showCompleted is false", async () => {
      const jobs = [
        createSyncJob({ id: "1", status: "completed" }),
        createSyncJob({ id: "2", status: "pending" }),
      ];
      mockApiRequest.mockResolvedValue(jobs);
      render(<ImageSyncProgress showCompleted={false} />);

      await waitFor(() => {
        expect(screen.getByText("pending")).toBeInTheDocument();
      });

      expect(screen.queryByText("completed")).not.toBeInTheDocument();
    });

    it("filters out failed jobs when showCompleted is false", async () => {
      const jobs = [
        createSyncJob({ id: "1", status: "failed" }),
        createSyncJob({ id: "2", status: "pending" }),
      ];
      mockApiRequest.mockResolvedValue(jobs);
      render(<ImageSyncProgress showCompleted={false} />);

      await waitFor(() => {
        expect(screen.getByText("pending")).toBeInTheDocument();
      });

      expect(screen.queryByText("failed")).not.toBeInTheDocument();
    });

    it("filters out cancelled jobs when showCompleted is false", async () => {
      const jobs = [
        createSyncJob({ id: "1", status: "cancelled" }),
        createSyncJob({ id: "2", status: "pending" }),
      ];
      mockApiRequest.mockResolvedValue(jobs);
      render(<ImageSyncProgress showCompleted={false} />);

      await waitFor(() => {
        expect(screen.getByText("pending")).toBeInTheDocument();
      });

      expect(screen.queryByText("cancelled")).not.toBeInTheDocument();
    });
  });

  describe("auto-refresh", () => {
    it("sets up auto-refresh when there are active jobs", async () => {
      const job = createSyncJob({ status: "transferring" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress autoRefreshInterval={1000} />);

      await waitFor(() => {
        expect(screen.getByText("transferring")).toBeInTheDocument();
      });

      // Clear mock and advance timer
      mockApiRequest.mockClear();
      mockApiRequest.mockResolvedValue([job]);

      await vi.advanceTimersByTimeAsync(1000);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalled();
      });
    });

    it("does not auto-refresh when no active jobs", async () => {
      const job = createSyncJob({ status: "completed" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress autoRefreshInterval={1000} />);

      await waitFor(() => {
        expect(screen.getByText("completed")).toBeInTheDocument();
      });

      // Clear mock and advance timer
      mockApiRequest.mockClear();

      await vi.advanceTimersByTimeAsync(1000);

      // Should not have been called again
      expect(mockApiRequest).not.toHaveBeenCalled();
    });

    it("uses default refresh interval of 2000ms", async () => {
      const job = createSyncJob({ status: "pending" });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("pending")).toBeInTheDocument();
      });

      mockApiRequest.mockClear();
      mockApiRequest.mockResolvedValue([job]);

      // Advance less than 2000ms - should not refresh
      await vi.advanceTimersByTimeAsync(1500);
      expect(mockApiRequest).not.toHaveBeenCalled();

      // Advance to 2000ms - should refresh
      await vi.advanceTimersByTimeAsync(500);
      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalled();
      });
    });
  });

  describe("onJobComplete callback", () => {
    it("calls onJobComplete when a job transitions to completed", async () => {
      const onJobComplete = vi.fn();
      const job1 = createSyncJob({ id: "job-1", status: "transferring" });
      const job2 = createSyncJob({ id: "job-1", status: "completed" });

      mockApiRequest.mockResolvedValueOnce([job1]);
      render(
        <ImageSyncProgress onJobComplete={onJobComplete} autoRefreshInterval={1000} />
      );

      await waitFor(() => {
        expect(screen.getByText("transferring")).toBeInTheDocument();
      });

      mockApiRequest.mockResolvedValueOnce([job2]);
      await vi.advanceTimersByTimeAsync(1000);

      await waitFor(() => {
        expect(onJobComplete).toHaveBeenCalledWith(job2);
      });
    });

    it("does not call onJobComplete for already completed jobs", async () => {
      const onJobComplete = vi.fn();
      const job = createSyncJob({ id: "job-1", status: "completed" });

      mockApiRequest.mockResolvedValue([job]);
      render(
        <ImageSyncProgress onJobComplete={onJobComplete} />
      );

      await waitFor(() => {
        expect(screen.getByText("completed")).toBeInTheDocument();
      });

      expect(onJobComplete).not.toHaveBeenCalled();
    });
  });

  describe("bytes formatting", () => {
    it("formats bytes correctly", async () => {
      const job = createSyncJob({
        status: "transferring",
        bytes_transferred: 1536,
        total_bytes: 2048,
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        // parseFloat strips trailing zeros: "1.5 KB" and "2 KB"
        expect(screen.getByText("1.5 KB / 2 KB")).toBeInTheDocument();
      });
    });

    it("formats megabytes correctly", async () => {
      const job = createSyncJob({
        status: "transferring",
        bytes_transferred: 52428800, // 50MB
        total_bytes: 104857600, // 100MB
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        // parseFloat strips trailing zeros: "50 MB" and "100 MB"
        expect(screen.getByText("50 MB / 100 MB")).toBeInTheDocument();
      });
    });

    it("formats gigabytes correctly", async () => {
      const job = createSyncJob({
        status: "transferring",
        bytes_transferred: 1610612736, // 1.5GB
        total_bytes: 2147483648, // 2GB
      });
      mockApiRequest.mockResolvedValue([job]);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        // parseFloat strips trailing zeros: "1.5 GB" and "2 GB"
        expect(screen.getByText("1.5 GB / 2 GB")).toBeInTheDocument();
      });
    });
  });

  describe("multiple jobs", () => {
    it("renders multiple jobs", async () => {
      const jobs = [
        createSyncJob({ id: "1", host_name: "Host 1", status: "pending" }),
        createSyncJob({ id: "2", host_name: "Host 2", status: "transferring" }),
        createSyncJob({ id: "3", host_name: "Host 3", status: "completed" }),
      ];
      mockApiRequest.mockResolvedValue(jobs);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        expect(screen.getByText("Host 1")).toBeInTheDocument();
        expect(screen.getByText("Host 2")).toBeInTheDocument();
        expect(screen.getByText("Host 3")).toBeInTheDocument();
      });
    });

    it("renders jobs in order", async () => {
      const jobs = [
        createSyncJob({ id: "1", image_id: "image-a" }),
        createSyncJob({ id: "2", image_id: "image-b" }),
      ];
      mockApiRequest.mockResolvedValue(jobs);
      render(<ImageSyncProgress />);

      await waitFor(() => {
        const imageIds = screen.getAllByText(/image-/);
        expect(imageIds[0]).toHaveTextContent("image-a");
        expect(imageIds[1]).toHaveTextContent("image-b");
      });
    });
  });
});
