/**
 * Tests for on-demand sync jobs appearing in the ImageSyncProgress component.
 *
 * Verifies that sync jobs triggered by node start operations are displayed
 * correctly in the sync jobs view with proper status, host name, and
 * concurrent rendering.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ImageSyncProgress from "./ImageSyncProgress";

// Mock the api module
vi.mock("../api", () => ({
  apiRequest: vi.fn(),
}));

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

const createOnDemandSyncJob = (overrides: Partial<SyncJob> = {}): SyncJob => ({
  id: "on-demand-job-1",
  image_id: "docker:ceos:4.28.0F",
  host_id: "remote-agent-1",
  host_name: "Remote Agent",
  status: "transferring",
  progress_percent: 35,
  bytes_transferred: 376832000,
  total_bytes: 1073741824,
  error_message: null,
  started_at: "2024-01-15T10:00:00Z",
  completed_at: null,
  created_at: "2024-01-15T09:59:50Z",
  ...overrides,
});

describe("ImageSyncProgress - On-Demand Sync Jobs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("displays on-demand sync job triggered by node start", async () => {
    const job = createOnDemandSyncJob({
      status: "transferring",
      progress_percent: 35,
    });
    mockApiRequest.mockResolvedValue([job]);
    render(<ImageSyncProgress />);

    await waitFor(() => {
      // Verify status is rendered
      expect(screen.getByText("transferring")).toBeInTheDocument();
      // Verify progress percentage
      expect(screen.getByText("35%")).toBeInTheDocument();
    });
  });

  it("shows job with correct host name and image reference", async () => {
    const job = createOnDemandSyncJob({
      host_name: "Remote Agent",
      image_id: "docker:ceos:4.28.0F",
    });
    mockApiRequest.mockResolvedValue([job]);
    render(<ImageSyncProgress />);

    await waitFor(() => {
      // Host name should be displayed
      expect(screen.getByText("Remote Agent")).toBeInTheDocument();
      // Image ID should be displayed
      expect(screen.getByText("docker:ceos:4.28.0F")).toBeInTheDocument();
    });
  });

  it("shows multiple concurrent on-demand sync jobs", async () => {
    const ceosJob = createOnDemandSyncJob({
      id: "job-ceos",
      image_id: "docker:ceos:4.28.0F",
      host_name: "Agent-1",
      status: "transferring",
      progress_percent: 60,
    });
    const srlJob = createOnDemandSyncJob({
      id: "job-srl",
      image_id: "docker:ghcr.io/nokia/srlinux:23.10.1",
      host_name: "Agent-2",
      status: "pending",
      progress_percent: 0,
    });

    mockApiRequest.mockResolvedValue([ceosJob, srlJob]);
    render(<ImageSyncProgress />);

    await waitFor(() => {
      // Both host names should be displayed
      expect(screen.getByText("Agent-1")).toBeInTheDocument();
      expect(screen.getByText("Agent-2")).toBeInTheDocument();

      // Both image IDs should be displayed
      expect(screen.getByText("docker:ceos:4.28.0F")).toBeInTheDocument();
      expect(
        screen.getByText("docker:ghcr.io/nokia/srlinux:23.10.1")
      ).toBeInTheDocument();
    });
  });
});
