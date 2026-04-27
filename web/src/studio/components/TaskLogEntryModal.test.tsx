import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import TaskLogEntryModal from "./TaskLogEntryModal";
import type { TaskLogEntry } from "./TaskLogPanel";

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
  }) =>
    isOpen ? (
      <div data-testid="detail-popup">
        <h2>{title}</h2>
        <button onClick={onClose}>Close</button>
        {children}
      </div>
    ) : null,
}));

function makeEntry(overrides: Partial<TaskLogEntry> = {}): TaskLogEntry {
  return {
    id: "entry-1",
    timestamp: new Date("2026-03-01T08:00:00Z"),
    level: "warning",
    message: "sync failed on remote host",
    jobId: "job-123",
    ...overrides,
  };
}

describe("TaskLogEntryModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("renders empty state when entry is null", () => {
    render(
      <TaskLogEntryModal
        isOpen={true}
        onClose={vi.fn()}
        entry={null}
      />
    );

    expect(screen.getByText("Task Log")).toBeInTheDocument();
    expect(screen.getByText("No entry selected.")).toBeInTheDocument();
  });

  it("renders entry metadata and message", () => {
    render(
      <TaskLogEntryModal
        isOpen={true}
        onClose={vi.fn()}
        entry={makeEntry({ level: "error", message: "worker timeout" })}
      />
    );

    expect(screen.getByText("Task Log (error)")).toBeInTheDocument();
    expect(screen.getByText("worker timeout")).toBeInTheDocument();
    expect(screen.getByText(/job: job-123/)).toBeInTheDocument();
  });

  it("copies via clipboard API when available", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <TaskLogEntryModal
        isOpen={true}
        onClose={vi.fn()}
        entry={makeEntry()}
      />
    );

    await user.click(screen.getByRole("button", { name: "Copy" }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument();
    });

    const copied = String(writeText.mock.calls[0][0]);
    expect(copied).toContain("[WARNING]");
    expect(copied).toContain("sync failed on remote host");
    expect(copied).toContain("job-123");
  });

  it("falls back to execCommand copy when clipboard API is unavailable", async () => {
    const user = userEvent.setup();
    const execCommand = vi.fn(() => true);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(
      <TaskLogEntryModal
        isOpen={true}
        onClose={vi.fn()}
        entry={makeEntry()}
      />
    );

    await user.click(screen.getByRole("button", { name: "Copy" }));

    expect(execCommand).toHaveBeenCalledWith("copy");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument();
    });
  });

  it("shows copy failure when clipboard and fallback both fail", async () => {
    const user = userEvent.setup();
    const execCommand = vi.fn(() => false);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(
      <TaskLogEntryModal
        isOpen={true}
        onClose={vi.fn()}
        entry={makeEntry()}
      />
    );

    await user.click(screen.getByRole("button", { name: "Copy" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Copy failed" })).toBeInTheDocument();
    });
  });

  it("falls back to execCommand when clipboard.writeText rejects", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    const execCommand = vi.fn(() => true);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(
      <TaskLogEntryModal isOpen onClose={vi.fn()} entry={makeEntry()} />,
    );

    await user.click(screen.getByRole("button", { name: "Copy" }));

    expect(writeText).toHaveBeenCalled();
    await waitFor(() => {
      expect(execCommand).toHaveBeenCalledWith("copy");
      expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument();
    });
  });

  it("returns to idle copy label after the 2s timer fires", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");

    render(
      <TaskLogEntryModal isOpen onClose={vi.fn()} entry={makeEntry()} />,
    );

    await user.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument();
    });

    // Reach into the 2s callback and run it directly to avoid mixing
    // fake timers with the user-event/waitFor tooling.
    const twoSecondCall = setTimeoutSpy.mock.calls.find(
      (c) => c[1] === 2000,
    );
    expect(twoSecondCall).toBeDefined();
    act(() => {
      (twoSecondCall![0] as () => void)();
    });

    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    setTimeoutSpy.mockRestore();
  });

  it("clears any prior reset timer when copy is invoked again", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");

    render(
      <TaskLogEntryModal isOpen onClose={vi.fn()} entry={makeEntry()} />,
    );

    // First copy schedules a reset timer
    await user.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument();
    });

    const beforeSecond = clearTimeoutSpy.mock.calls.length;

    // Second copy must clear the existing timer before scheduling a new one
    await user.click(screen.getByRole("button", { name: "Copied!" }));
    await waitFor(() => {
      expect(clearTimeoutSpy.mock.calls.length).toBeGreaterThan(beforeSecond);
    });

    clearTimeoutSpy.mockRestore();
  });

  it("clears the pending reset timer on unmount", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");

    const { unmount } = render(
      <TaskLogEntryModal isOpen onClose={vi.fn()} entry={makeEntry()} />,
    );

    await user.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Copied!" })).toBeInTheDocument();
    });

    // A success timer is now pending; unmounting should clear it via the
    // useEffect cleanup branch.
    unmount();
    expect(clearTimeoutSpy).toHaveBeenCalled();

    clearTimeoutSpy.mockRestore();
  });
});
