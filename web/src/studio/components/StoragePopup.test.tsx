import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StoragePopup from "./StoragePopup";

describe("StoragePopup", () => {
  const mockOnClose = vi.fn();

  const mockPerHost = [
    {
      id: "host-1",
      name: "primary-host",
      storage_percent: 45.0,
      storage_used_gb: 450.0,
      storage_total_gb: 1000.0,
    },
    {
      id: "host-2",
      name: "secondary-host",
      storage_percent: 78.0,
      storage_used_gb: 780.0,
      storage_total_gb: 1000.0,
    },
    {
      id: "host-3",
      name: "high-usage-host",
      storage_percent: 92.0,
      storage_used_gb: 1840.0,
      storage_total_gb: 2000.0,
    },
  ];

  const mockTotals = {
    used_gb: 3070.0,
    total_gb: 4000.0,
    percent: 76.75,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when isOpen is false", () => {
    const { container } = render(
      <StoragePopup
        isOpen={false}
        onClose={mockOnClose}
        perHost={mockPerHost}
        totals={mockTotals}
      />
    );

    expect(container.firstChild).toBeNull();
  });

  it("displays popup title as 'Storage Usage'", () => {
    render(
      <StoragePopup
        isOpen={true}
        onClose={mockOnClose}
        perHost={mockPerHost}
        totals={mockTotals}
      />
    );

    expect(screen.getByText("Storage Usage")).toBeInTheDocument();
  });

  describe("total summary", () => {
    it("displays 'Total Storage' label", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      expect(screen.getByText("Total Storage")).toBeInTheDocument();
    });

    it("displays total storage size", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      // 3070 GB / 4000 GB -> 3.1TB / 4.0TB
      expect(screen.getByText(/3\.1TB \/ 4\.0TB/)).toBeInTheDocument();
    });

    it("displays total percentage used", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      expect(screen.getByText("76.8% used")).toBeInTheDocument();
    });

    it("renders total storage progress bar", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      // Check for progress bar background
      const progressBars = document.querySelectorAll(".h-4.bg-stone-200");
      expect(progressBars.length).toBeGreaterThan(0);
    });
  });

  describe("per-host breakdown", () => {
    it("displays 'By Host' section header when multiple hosts", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      expect(screen.getByText("By Host")).toBeInTheDocument();
    });

    it("displays all host names", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      expect(screen.getByText("primary-host")).toBeInTheDocument();
      expect(screen.getByText("secondary-host")).toBeInTheDocument();
      expect(screen.getByText("high-usage-host")).toBeInTheDocument();
    });

    it("displays host storage sizes", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      // primary-host: 450/1000 GB - formatStorageSize converts GB >= 1000 to TB
      expect(screen.getByText(/450\.0GB/)).toBeInTheDocument();
      expect(screen.getAllByText(/1\.0TB/).length).toBeGreaterThan(0); // 1000 GB = 1.0TB
      // secondary-host: 780/1000 GB
      expect(screen.getByText(/780\.0GB/)).toBeInTheDocument();
      // high-usage-host: 1840/2000 GB -> 1.8TB / 2.0TB
      expect(screen.getByText(/1\.8TB/)).toBeInTheDocument();
      expect(screen.getByText(/2\.0TB/)).toBeInTheDocument();
    });

    it("displays host storage percentages", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      expect(screen.getByText("45.0%")).toBeInTheDocument();
      expect(screen.getByText("78.0%")).toBeInTheDocument();
      expect(screen.getByText("92.0%")).toBeInTheDocument();
    });

    it("renders progress bars for each host", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      // Should have 4 progress bar backgrounds (1 total + 3 hosts)
      const progressBars = document.querySelectorAll(".h-4.bg-stone-200");
      expect(progressBars.length).toBe(4);
    });
  });

  describe("single host environment", () => {
    it("shows single-host info message", () => {
      const singleHost = [mockPerHost[0]];

      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={singleHost}
          totals={mockTotals}
        />
      );

      expect(
        screen.getByText(/Single-host environment: primary-host/)
      ).toBeInTheDocument();
    });

    it("does not show 'By Host' section for single host", () => {
      const singleHost = [mockPerHost[0]];

      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={singleHost}
          totals={mockTotals}
        />
      );

      expect(screen.queryByText("By Host")).not.toBeInTheDocument();
    });
  });

  describe("no hosts", () => {
    it("shows no data message when perHost is empty", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={[]}
          totals={mockTotals}
        />
      );

      expect(screen.getByText("No storage data available")).toBeInTheDocument();
    });

    it("does not show 'By Host' section when no hosts", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={[]}
          totals={mockTotals}
        />
      );

      expect(screen.queryByText("By Host")).not.toBeInTheDocument();
    });
  });

  describe("thresholds legend", () => {
    it("displays thresholds section", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      expect(screen.getByText("Thresholds")).toBeInTheDocument();
    });

    it("displays threshold labels", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      expect(screen.getByText("Normal")).toBeInTheDocument();
      expect(screen.getByText("75%+")).toBeInTheDocument();
      expect(screen.getByText("90%+")).toBeInTheDocument();
    });

    it("displays threshold color indicators", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      // Legend color indicators
      const violetIndicator = document.querySelector(".bg-violet-500");
      const amberIndicator = document.querySelector(".w-3.h-3.bg-amber-500");
      const redIndicator = document.querySelector(".w-3.h-3.bg-red-500");

      expect(violetIndicator).toBeInTheDocument();
      expect(amberIndicator).toBeInTheDocument();
      expect(redIndicator).toBeInTheDocument();
    });
  });

  describe("color thresholds on bars", () => {
    it("applies violet color for normal usage (< 75%)", () => {
      const normalUsageHost = [
        {
          id: "host-1",
          name: "normal-host",
          storage_percent: 45.0,
          storage_used_gb: 450.0,
          storage_total_gb: 1000.0,
        },
      ];

      const { container } = render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={normalUsageHost}
          totals={{ used_gb: 450, total_gb: 1000, percent: 45 }}
        />
      );

      // Both total and single-host message case, but total bar should be violet
      const violetBars = container.querySelectorAll(
        ".h-full.bg-violet-500"
      );
      expect(violetBars.length).toBeGreaterThan(0);
    });

    it("applies amber color for warning usage (75-90%)", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      // secondary-host at 78% should have amber
      // Total at 76.75% should also have amber
      const amberBars = document.querySelectorAll(".h-full.bg-amber-500");
      expect(amberBars.length).toBeGreaterThan(0);
    });

    it("applies red color for danger usage (>= 90%)", () => {
      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={mockPerHost}
          totals={mockTotals}
        />
      );

      // high-usage-host at 92% should have red
      const redBars = document.querySelectorAll(".h-full.bg-red-500");
      expect(redBars.length).toBeGreaterThan(0);
    });
  });

  it("calls onClose when close button is clicked", async () => {
    const user = userEvent.setup();

    render(
      <StoragePopup
        isOpen={true}
        onClose={mockOnClose}
        perHost={mockPerHost}
        totals={mockTotals}
      />
    );

    const closeButton = screen.getByRole("button");
    await user.click(closeButton);

    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  describe("edge cases", () => {
    it("handles 0% storage usage", () => {
      const emptyTotals = {
        used_gb: 0,
        total_gb: 1000,
        percent: 0,
      };

      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={[]}
          totals={emptyTotals}
        />
      );

      expect(screen.getByText("0.0% used")).toBeInTheDocument();
      expect(screen.getByText(/0\.0GB/)).toBeInTheDocument();
      expect(screen.getByText(/1\.0TB/)).toBeInTheDocument(); // 1000 GB = 1.0TB
    });

    it("handles 100% storage usage", () => {
      const fullTotals = {
        used_gb: 1000,
        total_gb: 1000,
        percent: 100,
      };

      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={[]}
          totals={fullTotals}
        />
      );

      expect(screen.getByText("100.0% used")).toBeInTheDocument();
    });

    it("caps progress bar width at 100%", () => {
      const overTotals = {
        used_gb: 1200,
        total_gb: 1000,
        percent: 120,
      };

      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={[]}
          totals={overTotals}
        />
      );

      // The component uses Math.min(percent, 100) for width
      const progressFill = document.querySelector(".h-full.bg-red-500");
      expect(progressFill).toBeInTheDocument();
      expect(progressFill).toHaveStyle({ width: "100%" });
    });

    it("formats large storage values as TB", () => {
      const largeStorage = [
        {
          id: "host-1",
          name: "large-host",
          storage_percent: 50.0,
          storage_used_gb: 5000.0,
          storage_total_gb: 10000.0,
        },
      ];

      const largeTotals = {
        used_gb: 5000,
        total_gb: 10000,
        percent: 50,
      };

      render(
        <StoragePopup
          isOpen={true}
          onClose={mockOnClose}
          perHost={largeStorage}
          totals={largeTotals}
        />
      );

      // 5000 GB = 5.0 TB, 10000 GB = 10.0 TB
      expect(screen.getByText(/5\.0TB \/ 10\.0TB/)).toBeInTheDocument();
    });
  });
});
