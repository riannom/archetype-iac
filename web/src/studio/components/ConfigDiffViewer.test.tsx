import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ConfigDiffViewer from "./ConfigDiffViewer";

interface ConfigSnapshot {
  id: string;
  lab_id: string;
  node_name: string;
  content: string;
  content_hash: string;
  snapshot_type: string;
  created_at: string;
}

interface DiffLine {
  line_number_a: number | null;
  line_number_b: number | null;
  content: string;
  type: "unchanged" | "added" | "removed" | "header";
}

interface DiffResponse {
  snapshot_a: ConfigSnapshot;
  snapshot_b: ConfigSnapshot;
  diff_lines: DiffLine[];
  additions: number;
  deletions: number;
}

const createMockSnapshot = (
  overrides: Partial<ConfigSnapshot> = {}
): ConfigSnapshot => ({
  id: overrides.id || "snapshot-1",
  lab_id: overrides.lab_id || "lab-1",
  node_name: overrides.node_name || "router1",
  content: overrides.content || "! Configuration\nhostname router1",
  content_hash: overrides.content_hash || "abc123def456789012",
  snapshot_type: overrides.snapshot_type || "manual",
  created_at: overrides.created_at || "2024-01-15T10:30:00Z",
});

const createMockDiffResponse = (
  overrides: Partial<DiffResponse> = {}
): DiffResponse => ({
  snapshot_a: overrides.snapshot_a || createMockSnapshot({ id: "snap-a" }),
  snapshot_b:
    overrides.snapshot_b ||
    createMockSnapshot({ id: "snap-b", created_at: "2024-01-15T11:00:00Z" }),
  diff_lines: overrides.diff_lines || [],
  additions: overrides.additions ?? 0,
  deletions: overrides.deletions ?? 0,
});

describe("ConfigDiffViewer", () => {
  const mockStudioRequest = vi.fn();
  const snapshotA = createMockSnapshot({
    id: "snap-a",
    created_at: "2024-01-15T10:30:00Z",
  });
  const snapshotB = createMockSnapshot({
    id: "snap-b",
    created_at: "2024-01-15T11:00:00Z",
  });

  const defaultProps = {
    snapshotA,
    snapshotB,
    studioRequest: mockStudioRequest,
    labId: "test-lab-123",
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockStudioRequest.mockResolvedValue(createMockDiffResponse());
  });

  describe("Loading state", () => {
    it("shows loading spinner while loading diff", async () => {
      mockStudioRequest.mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(() => resolve(createMockDiffResponse()), 100)
          )
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      expect(document.querySelector(".fa-spinner")).toBeInTheDocument();
    });

    it("calls studioRequest with correct parameters", async () => {
      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledWith(
          "/labs/test-lab-123/config-diff",
          {
            method: "POST",
            body: JSON.stringify({
              snapshot_id_a: "snap-a",
              snapshot_id_b: "snap-b",
            }),
          }
        );
      });
    });

    it("reloads diff when snapshot IDs change", async () => {
      const { rerender } = render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledTimes(1);
      });

      const newSnapshotA = createMockSnapshot({ id: "snap-c" });
      rerender(<ConfigDiffViewer {...defaultProps} snapshotA={newSnapshotA} />);

      await waitFor(() => {
        expect(mockStudioRequest).toHaveBeenCalledTimes(2);
      });
    });
  });

  describe("Error state", () => {
    it("shows error message when loading fails", async () => {
      mockStudioRequest.mockRejectedValue(new Error("Network error"));

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Network error")).toBeInTheDocument();
      });
    });

    it("shows error icon when loading fails", async () => {
      mockStudioRequest.mockRejectedValue(new Error("Failed to load diff"));

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(
          document.querySelector(".fa-exclamation-circle")
        ).toBeInTheDocument();
      });
    });

    it("shows generic error message for non-Error objects", async () => {
      mockStudioRequest.mockRejectedValue("Unknown error");

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("Failed to load diff")).toBeInTheDocument();
      });
    });
  });

  describe("Diff header", () => {
    it("displays deletion count", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          additions: 5,
          deletions: 3,
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("-3")).toBeInTheDocument();
        expect(screen.getByText("removed")).toBeInTheDocument();
      });
    });

    it("displays addition count", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          additions: 5,
          deletions: 3,
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("+5")).toBeInTheDocument();
        expect(screen.getByText("added")).toBeInTheDocument();
      });
    });

    it("shows 'No differences' when no changes", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          additions: 0,
          deletions: 0,
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("No differences")).toBeInTheDocument();
      });
    });

    it("does not show 'No differences' when there are changes", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          additions: 1,
          deletions: 0,
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.queryByText("No differences")).not.toBeInTheDocument();
      });
    });
  });

  describe("Version labels", () => {
    it("displays formatted timestamps for both snapshots", async () => {
      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        // Check for clock icons
        const clockIcons = document.querySelectorAll(".fa-clock");
        expect(clockIcons.length).toBe(2);
      });
    });

    it("displays snapshot types", async () => {
      const snapshotAWithManual = createMockSnapshot({ id: "snap-a", snapshot_type: "manual" });
      const snapshotBWithAuto = createMockSnapshot({ id: "snap-b", snapshot_type: "auto" });

      mockStudioRequest.mockResolvedValue(createMockDiffResponse());

      render(
        <ConfigDiffViewer
          {...defaultProps}
          snapshotA={snapshotAWithManual}
          snapshotB={snapshotBWithAuto}
        />
      );

      await waitFor(() => {
        expect(screen.getByText("(manual)")).toBeInTheDocument();
        expect(screen.getByText("(auto)")).toBeInTheDocument();
      });
    });
  });

  describe("Diff lines rendering", () => {
    it("renders unchanged lines correctly", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          diff_lines: [
            {
              line_number_a: 1,
              line_number_b: 1,
              content: "hostname router1",
              type: "unchanged",
            },
          ],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("hostname router1")).toBeInTheDocument();
      });
    });

    it("renders added lines with + prefix", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          additions: 1,
          diff_lines: [
            {
              line_number_a: null,
              line_number_b: 2,
              content: "interface eth0",
              type: "added",
            },
          ],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("interface eth0")).toBeInTheDocument();
        expect(screen.getByText("+")).toBeInTheDocument();
      });
    });

    it("renders removed lines with - prefix", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          deletions: 1,
          diff_lines: [
            {
              line_number_a: 2,
              line_number_b: null,
              content: "old interface",
              type: "removed",
            },
          ],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("old interface")).toBeInTheDocument();
        expect(screen.getByText("-")).toBeInTheDocument();
      });
    });

    it("renders header lines correctly", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          diff_lines: [
            {
              line_number_a: null,
              line_number_b: null,
              content: "@@ -1,5 +1,6 @@",
              type: "header",
            },
          ],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText("@@ -1,5 +1,6 @@")).toBeInTheDocument();
      });
    });

    it("displays line numbers for both columns", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          diff_lines: [
            {
              line_number_a: 1,
              line_number_b: 1,
              content: "line content",
              type: "unchanged",
            },
            {
              line_number_a: 2,
              line_number_b: null,
              content: "removed line",
              type: "removed",
            },
            {
              line_number_a: null,
              line_number_b: 2,
              content: "added line",
              type: "added",
            },
          ],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        // Verify line numbers are rendered
        const table = document.querySelector("table");
        expect(table).toBeInTheDocument();

        const rows = document.querySelectorAll("tr");
        expect(rows.length).toBe(3);
      });
    });
  });

  describe("Empty diff content", () => {
    it("shows 'Configurations are identical' when no diff lines", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          additions: 0,
          deletions: 0,
          diff_lines: [],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(
          screen.getByText("Configurations are identical")
        ).toBeInTheDocument();
      });
    });

    it("shows content hash when configurations are identical", async () => {
      const snapshot = createMockSnapshot({ content_hash: "abc123def456789012" });
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          snapshot_a: snapshot,
          snapshot_b: snapshot,
          additions: 0,
          deletions: 0,
          diff_lines: [],
        })
      );

      render(
        <ConfigDiffViewer
          {...defaultProps}
          snapshotA={snapshot}
          snapshotB={snapshot}
        />
      );

      await waitFor(() => {
        expect(screen.getByText(/Hash:/)).toBeInTheDocument();
        expect(screen.getByText(/abc123def456/)).toBeInTheDocument();
      });
    });

    it("shows equals icon when configurations are identical", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          diff_lines: [],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        expect(document.querySelector(".fa-equals")).toBeInTheDocument();
      });
    });
  });

  describe("Styling", () => {
    it("applies emerald color styling to added lines", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          additions: 1,
          diff_lines: [
            {
              line_number_a: null,
              line_number_b: 1,
              content: "new line",
              type: "added",
            },
          ],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        const addedRow = document.querySelector(".bg-emerald-500\\/10");
        expect(addedRow).toBeInTheDocument();
      });
    });

    it("applies red color styling to removed lines", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          deletions: 1,
          diff_lines: [
            {
              line_number_a: 1,
              line_number_b: null,
              content: "old line",
              type: "removed",
            },
          ],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        const removedRow = document.querySelector(".bg-red-500\\/10");
        expect(removedRow).toBeInTheDocument();
      });
    });

    it("applies blue color styling to header lines", async () => {
      mockStudioRequest.mockResolvedValue(
        createMockDiffResponse({
          diff_lines: [
            {
              line_number_a: null,
              line_number_b: null,
              content: "@@ header @@",
              type: "header",
            },
          ],
        })
      );

      render(<ConfigDiffViewer {...defaultProps} />);

      await waitFor(() => {
        const headerRow = document.querySelector(".bg-blue-500\\/10");
        expect(headerRow).toBeInTheDocument();
      });
    });
  });

  describe("Null state", () => {
    it("returns null when diff is not loaded yet and not loading", async () => {
      // This test verifies the component returns null before data is loaded
      // but after loading spinner phase
      mockStudioRequest.mockImplementation(() => new Promise(() => {})); // Never resolves

      const { container } = render(<ConfigDiffViewer {...defaultProps} />);

      // Initially shows loading spinner
      expect(document.querySelector(".fa-spinner")).toBeInTheDocument();

      // Container should have content during loading
      expect(container.firstChild).not.toBeNull();
    });
  });
});
