import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SystemStatusStrip from "./SystemStatusStrip";
import { BrowserRouter } from "react-router-dom";
import { DEFAULT_USER_PREFERENCES } from "../../types/notifications";

// Mock useNotifications
const mockUpdateCanvasSettings = vi.fn();
const mockPreferences = { ...DEFAULT_USER_PREFERENCES };

vi.mock("../../contexts/NotificationContext", () => ({
  useNotifications: () => ({
    preferences: mockPreferences,
    updateCanvasSettings: mockUpdateCanvasSettings,
  }),
}));

// Mock the popup components
vi.mock("./AgentsPopup", () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) =>
    isOpen ? (
      <div data-testid="agents-popup">
        <button onClick={onClose}>Close Agents Popup</button>
      </div>
    ) : null,
}));

vi.mock("./ContainersPopup", () => ({
  default: ({
    isOpen,
    onClose,
    filterHostName,
  }: {
    isOpen: boolean;
    onClose: () => void;
    filterHostName?: string;
  }) =>
    isOpen ? (
      <div data-testid="containers-popup">
        {filterHostName && <span data-testid="host-filter">{filterHostName}</span>}
        <button onClick={onClose}>Close Containers Popup</button>
      </div>
    ) : null,
}));

vi.mock("./ResourcesPopup", () => ({
  default: ({
    isOpen,
    onClose,
    type,
  }: {
    isOpen: boolean;
    onClose: () => void;
    type: "cpu" | "memory";
  }) =>
    isOpen ? (
      <div data-testid="resources-popup">
        <span data-testid="resource-type">{type}</span>
        <button onClick={onClose}>Close Resources Popup</button>
      </div>
    ) : null,
}));

vi.mock("./StoragePopup", () => ({
  default: ({
    isOpen,
    onClose,
  }: {
    isOpen: boolean;
    onClose: () => void;
  }) =>
    isOpen ? (
      <div data-testid="storage-popup">
        <button onClick={onClose}>Close Storage Popup</button>
      </div>
    ) : null,
}));

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter>{children}</BrowserRouter>
);

const baseMetrics = {
  agents: { online: 2, total: 3 },
  containers: { running: 10, total: 15 },
  cpu_percent: 45.5,
  memory_percent: 62.3,
  labs_running: 1,
  labs_total: 5,
};

const metricsWithMemoryAndStorage = {
  ...baseMetrics,
  memory: {
    used_gb: 8.5,
    total_gb: 16.0,
    percent: 53.125,
  },
  storage: {
    used_gb: 100,
    total_gb: 500,
    percent: 20,
  },
};

const multiHostMetrics = {
  ...metricsWithMemoryAndStorage,
  is_multi_host: true,
  per_host: [
    {
      id: "host-1",
      name: "Host Alpha",
      cpu_percent: 30,
      memory_percent: 40,
      memory_used_gb: 4,
      memory_total_gb: 8,
      storage_percent: 25,
      storage_used_gb: 50,
      storage_total_gb: 200,
      containers_running: 5,
      started_at: "2026-01-30T10:00:00Z",
    },
    {
      id: "host-2",
      name: "Host Beta",
      cpu_percent: 60,
      memory_percent: 80,
      memory_used_gb: 12,
      memory_total_gb: 16,
      storage_percent: 50,
      storage_used_gb: 150,
      storage_total_gb: 300,
      containers_running: 5,
      started_at: "2026-01-30T08:00:00Z",
    },
  ],
};

describe("SystemStatusStrip", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset preferences to default state
    mockPreferences.canvas_settings.metricsBarExpanded = false;
  });

  describe("Loading state", () => {
    it("shows loading message when metrics are null", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={null} />
        </TestWrapper>
      );

      expect(screen.getByText("Loading system status...")).toBeInTheDocument();
    });
  });

  describe("Agents display", () => {
    it("displays agent count correctly", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      expect(screen.getByText("2")).toBeInTheDocument();
      expect(screen.getByText("/3")).toBeInTheDocument();
      expect(screen.getByText("agents")).toBeInTheDocument();
    });

    it("shows green indicator when all agents are online", () => {
      const allOnline = { ...baseMetrics, agents: { online: 3, total: 3 } };
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={allOnline} />
        </TestWrapper>
      );

      const greenIndicator = document.querySelector(".bg-green-500.animate-pulse");
      expect(greenIndicator).toBeInTheDocument();
    });

    it("shows amber indicator when some agents are offline", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      const amberIndicator = document.querySelector(".bg-amber-500");
      expect(amberIndicator).toBeInTheDocument();
    });

    it("navigates to hosts page when agents section is clicked", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      const agentsButton = screen.getByText("agents").closest("button");
      await user.click(agentsButton!);

      expect(mockNavigate).toHaveBeenCalledWith("/hosts");
    });
  });

  describe("Containers display", () => {
    it("displays container count correctly", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      expect(screen.getByText("10")).toBeInTheDocument();
      expect(screen.getByText("/15")).toBeInTheDocument();
      expect(screen.getByText("containers")).toBeInTheDocument();
    });

    it("opens containers popup when clicked", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      const containersButton = screen.getByText("containers").closest("button");
      await user.click(containersButton!);

      expect(screen.getByTestId("containers-popup")).toBeInTheDocument();
    });

    it("closes containers popup when close is triggered", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      const containersButton = screen.getByText("containers").closest("button");
      await user.click(containersButton!);

      expect(screen.getByTestId("containers-popup")).toBeInTheDocument();

      await user.click(screen.getByText("Close Containers Popup"));

      expect(screen.queryByTestId("containers-popup")).not.toBeInTheDocument();
    });
  });

  describe("Labs display", () => {
    it("displays labs running count correctly", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      expect(screen.getByText("1")).toBeInTheDocument();
      expect(screen.getByText("/5")).toBeInTheDocument();
      expect(screen.getByText("labs running")).toBeInTheDocument();
    });
  });

  describe("CPU display", () => {
    it("displays CPU percentage correctly", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      expect(screen.getByText("CPU")).toBeInTheDocument();
      expect(screen.getByText("46%")).toBeInTheDocument();
    });

    it("opens CPU resources popup when clicked", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      const cpuButton = screen.getByText("CPU").closest("button");
      await user.click(cpuButton!);

      expect(screen.getByTestId("resources-popup")).toBeInTheDocument();
      expect(screen.getByTestId("resource-type")).toHaveTextContent("cpu");
    });
  });

  describe("Memory display", () => {
    it("displays memory percentage correctly", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      expect(screen.getByText("MEM")).toBeInTheDocument();
      expect(screen.getByText("62%")).toBeInTheDocument();
    });

    it("displays memory usage details when available", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={metricsWithMemoryAndStorage} />
        </TestWrapper>
      );

      expect(screen.getByText("8.5 GB/16.0 GB")).toBeInTheDocument();
    });

    it("opens memory resources popup when clicked", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      const memButton = screen.getByText("MEM").closest("button");
      await user.click(memButton!);

      expect(screen.getByTestId("resources-popup")).toBeInTheDocument();
      expect(screen.getByTestId("resource-type")).toHaveTextContent("memory");
    });
  });

  describe("Storage display", () => {
    it("does not show storage when not available", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={baseMetrics} />
        </TestWrapper>
      );

      expect(screen.queryByText("DISK")).not.toBeInTheDocument();
    });

    it("displays storage when available", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={metricsWithMemoryAndStorage} />
        </TestWrapper>
      );

      expect(screen.getByText("DISK")).toBeInTheDocument();
      expect(screen.getByText("20%")).toBeInTheDocument();
      expect(screen.getByText("100.0 GB/500.0 GB")).toBeInTheDocument();
    });

    it("opens storage popup when clicked", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={metricsWithMemoryAndStorage} />
        </TestWrapper>
      );

      const diskButton = screen.getByText("DISK").closest("button");
      await user.click(diskButton!);

      expect(screen.getByTestId("storage-popup")).toBeInTheDocument();
    });
  });

  describe("Multi-host support", () => {
    it("shows aggregated badge when multi-host is enabled", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={multiHostMetrics} />
        </TestWrapper>
      );

      expect(screen.getByText("aggregated")).toBeInTheDocument();
      expect(screen.getByText("(2)")).toBeInTheDocument();
    });

    it("does not show aggregated badge when not multi-host", () => {
      render(
        <TestWrapper>
          <SystemStatusStrip metrics={metricsWithMemoryAndStorage} />
        </TestWrapper>
      );

      expect(screen.queryByText("aggregated")).not.toBeInTheDocument();
    });

    it("calls updateCanvasSettings when aggregated is clicked to expand", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={multiHostMetrics} />
        </TestWrapper>
      );

      const aggregatedButton = screen.getByText("aggregated").closest("button");
      await user.click(aggregatedButton!);

      expect(mockUpdateCanvasSettings).toHaveBeenCalledWith({ metricsBarExpanded: true });
    });

    it("calls updateCanvasSettings when aggregated is clicked to collapse", async () => {
      const user = userEvent.setup();
      // Start with expanded state
      mockPreferences.canvas_settings.metricsBarExpanded = true;

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={multiHostMetrics} />
        </TestWrapper>
      );

      const aggregatedButton = screen.getByText("aggregated").closest("button");
      await user.click(aggregatedButton!);

      expect(mockUpdateCanvasSettings).toHaveBeenCalledWith({ metricsBarExpanded: false });
    });

    it("shows expanded state when preference is true", () => {
      mockPreferences.canvas_settings.metricsBarExpanded = true;

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={multiHostMetrics} />
        </TestWrapper>
      );

      const aggregatedButton = screen.getByText("aggregated").closest("button");
      expect(aggregatedButton).toHaveAttribute("aria-expanded", "true");

      // Per-host section should be visible
      const perHostSection = screen.getByText("Host Alpha").closest("div[class*='overflow-hidden']");
      expect(perHostSection).toHaveClass("max-h-[500px]");
    });

    it("shows collapsed state when preference is false", () => {
      mockPreferences.canvas_settings.metricsBarExpanded = false;

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={multiHostMetrics} />
        </TestWrapper>
      );

      const aggregatedButton = screen.getByText("aggregated").closest("button");
      expect(aggregatedButton).toHaveAttribute("aria-expanded", "false");

      // Per-host section should be hidden via CSS
      const perHostSection = screen.getByText("Host Alpha").closest("div[class*='overflow-hidden']");
      expect(perHostSection).toHaveClass("max-h-0");
    });

    it("displays per-host metrics correctly", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={multiHostMetrics} />
        </TestWrapper>
      );

      const aggregatedButton = screen.getByText("aggregated").closest("button");
      await user.click(aggregatedButton!);

      // Check Host Alpha metrics
      expect(screen.getByText("Host Alpha")).toBeInTheDocument();

      // Check container count text exists for Host Alpha (5 containers)
      const containerButtons = screen.getAllByText("containers");
      expect(containerButtons.length).toBeGreaterThanOrEqual(2); // Main + per-host
    });

    it("opens containers popup with host filter when per-host container is clicked", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={multiHostMetrics} />
        </TestWrapper>
      );

      // Expand per-host rows
      const aggregatedButton = screen.getByText("aggregated").closest("button");
      await user.click(aggregatedButton!);

      // Click on Host Alpha's container button
      const hostAlphaRow = screen.getByText("Host Alpha").closest("div[class*='flex items-center']")?.parentElement;
      const containerButton = hostAlphaRow?.querySelector("button");
      await user.click(containerButton!);

      expect(screen.getByTestId("containers-popup")).toBeInTheDocument();
      expect(screen.getByTestId("host-filter")).toHaveTextContent("Host Alpha");
    });
  });

  describe("Memory size formatting", () => {
    it("formats terabytes correctly", () => {
      const tbMetrics = {
        ...baseMetrics,
        memory: {
          used_gb: 1500,
          total_gb: 2048,
          percent: 73.24,
        },
      };

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={tbMetrics} />
        </TestWrapper>
      );

      expect(screen.getByText("1.5 TB/2.0 TB")).toBeInTheDocument();
    });

    it("formats megabytes correctly", () => {
      const mbMetrics = {
        ...baseMetrics,
        memory: {
          used_gb: 0.5,
          total_gb: 0.75,
          percent: 66.67,
        },
      };

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={mbMetrics} />
        </TestWrapper>
      );

      expect(screen.getByText("512 MB/768 MB")).toBeInTheDocument();
    });
  });

  describe("Progress bar capping", () => {
    it("caps progress bar width at 100%", () => {
      const overMaxMetrics = {
        ...baseMetrics,
        cpu_percent: 150,
      };

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={overMaxMetrics} />
        </TestWrapper>
      );

      // The displayed text shows the actual value
      expect(screen.getByText("150%")).toBeInTheDocument();

      // But the progress bar should be capped at 100%
      const cpuProgressBar = document.querySelector('[style*="width: 100%"]');
      expect(cpuProgressBar).toBeInTheDocument();
    });
  });

  describe("Popup interactions", () => {
    it("only shows one popup at a time", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={metricsWithMemoryAndStorage} />
        </TestWrapper>
      );

      // Open containers popup
      const containersButton = screen.getByText("containers").closest("button");
      await user.click(containersButton!);
      expect(screen.getByTestId("containers-popup")).toBeInTheDocument();

      // Open CPU popup - should close containers popup
      const cpuButton = screen.getByText("CPU").closest("button");
      await user.click(cpuButton!);

      expect(screen.queryByTestId("containers-popup")).not.toBeInTheDocument();
      expect(screen.getByTestId("resources-popup")).toBeInTheDocument();
    });

    it("closes popup when close handler is called", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <SystemStatusStrip metrics={metricsWithMemoryAndStorage} />
        </TestWrapper>
      );

      // Open storage popup
      const diskButton = screen.getByText("DISK").closest("button");
      await user.click(diskButton!);
      expect(screen.getByTestId("storage-popup")).toBeInTheDocument();

      // Close it
      await user.click(screen.getByText("Close Storage Popup"));

      expect(screen.queryByTestId("storage-popup")).not.toBeInTheDocument();
    });
  });
});
