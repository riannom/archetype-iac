import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResourcesPopup from "./ResourcesPopup";

// Mock apiRequest
const mockApiRequest = vi.fn();
vi.mock("../../api", () => ({
  apiRequest: (...args: any[]) => mockApiRequest(...args),
}));

const mockResourcesData = {
  by_agent: [
    {
      id: "agent-1",
      name: "primary-agent",
      cpu_percent: 45.5,
      memory_percent: 62.3,
      memory_used_gb: 8.0,
      memory_total_gb: 16.0,
      containers: 10,
    },
    {
      id: "agent-2",
      name: "secondary-agent",
      cpu_percent: 75.0,
      memory_percent: 80.0,
      memory_used_gb: 24.0,
      memory_total_gb: 32.0,
      containers: 20,
    },
    {
      id: "agent-3",
      name: "high-load-agent",
      cpu_percent: 92.5,
      memory_percent: 95.0,
      memory_used_gb: 30.4,
      memory_total_gb: 32.0,
      containers: 30,
    },
  ],
  by_lab: [
    {
      id: "lab-1",
      name: "Test Lab 1",
      container_count: 5,
      estimated_percent: 25,
    },
    {
      id: "lab-2",
      name: "Production Lab",
      container_count: 15,
      estimated_percent: 50,
    },
    {
      id: "lab-3",
      name: "Dev Lab",
      container_count: 10,
      estimated_percent: 25,
    },
  ],
};

describe("ResourcesPopup", () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockApiRequest.mockResolvedValue(mockResourcesData);
  });

  describe("CPU mode", () => {
    it("renders nothing when isOpen is false", () => {
      const { container } = render(
        <ResourcesPopup isOpen={false} onClose={mockOnClose} type="cpu" />
      );

      expect(container.firstChild).toBeNull();
      expect(mockApiRequest).not.toHaveBeenCalled();
    });

    it("shows loading state when opened", () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      expect(screen.getByText("Loading...")).toBeInTheDocument();
    });

    it("fetches resources data when opened", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith("/dashboard/metrics/resources");
      });
    });

    it("displays correct title for CPU mode", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("CPU Usage Distribution")).toBeInTheDocument();
      });
    });

    it("displays agent section header", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("By Agent")).toBeInTheDocument();
      });
    });

    it("displays agent names", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("primary-agent")).toBeInTheDocument();
        expect(screen.getByText("secondary-agent")).toBeInTheDocument();
        expect(screen.getByText("high-load-agent")).toBeInTheDocument();
      });
    });

    it("displays CPU percentages in CPU mode", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText(/45\.5%/)).toBeInTheDocument();
        expect(screen.getByText(/75\.0%/)).toBeInTheDocument();
        expect(screen.getByText(/92\.5%/)).toBeInTheDocument();
      });
    });

    it("displays container counts", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        // Multiple elements may contain "10 containers" (both agent and lab)
        const container10Elements = screen.getAllByText(/10 containers/);
        expect(container10Elements.length).toBeGreaterThan(0);
        expect(screen.getByText(/20 containers/)).toBeInTheDocument();
        expect(screen.getByText(/30 containers/)).toBeInTheDocument();
      });
    });

    it("displays CPU thresholds legend", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("Thresholds")).toBeInTheDocument();
        expect(screen.getByText("Normal")).toBeInTheDocument();
        expect(screen.getByText("60%+")).toBeInTheDocument();
        expect(screen.getByText("80%+")).toBeInTheDocument();
      });
    });
  });

  describe("Memory mode", () => {
    it("displays correct title for Memory mode", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="memory" />);

      await waitFor(() => {
        expect(screen.getByText("Memory Usage Distribution")).toBeInTheDocument();
      });
    });

    it("displays memory percentages in memory mode", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="memory" />);

      await waitFor(() => {
        expect(screen.getByText(/62\.3%/)).toBeInTheDocument();
        expect(screen.getByText(/80\.0%/)).toBeInTheDocument();
        expect(screen.getByText(/95\.0%/)).toBeInTheDocument();
      });
    });

    it("displays memory size information", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="memory" />);

      await waitFor(() => {
        // primary-agent: 8.0/16.0 GB
        expect(screen.getByText(/8\.0 GB\/16\.0 GB/)).toBeInTheDocument();
        // secondary-agent: 24.0/32.0 GB
        expect(screen.getByText(/24\.0 GB\/32\.0 GB/)).toBeInTheDocument();
        // high-load-agent: 30.4/32.0 GB
        expect(screen.getByText(/30\.4 GB\/32\.0 GB/)).toBeInTheDocument();
      });
    });

    it("displays memory thresholds legend", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="memory" />);

      await waitFor(() => {
        expect(screen.getByText("Thresholds")).toBeInTheDocument();
        expect(screen.getByText("Normal")).toBeInTheDocument();
        expect(screen.getByText("70%+")).toBeInTheDocument();
        expect(screen.getByText("85%+")).toBeInTheDocument();
      });
    });
  });

  describe("Lab distribution", () => {
    it("displays lab section header", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("By Lab (Container Distribution)")).toBeInTheDocument();
      });
    });

    it("displays lab names", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
        expect(screen.getByText("Production Lab")).toBeInTheDocument();
        expect(screen.getByText("Dev Lab")).toBeInTheDocument();
      });
    });

    it("displays lab container counts and percentages", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText(/5 containers · 25%/)).toBeInTheDocument();
        expect(screen.getByText(/15 containers · 50%/)).toBeInTheDocument();
        expect(screen.getByText(/10 containers · 25%/)).toBeInTheDocument();
      });
    });
  });

  describe("empty states", () => {
    it("shows empty state when no agents online", async () => {
      mockApiRequest.mockResolvedValue({
        by_agent: [],
        by_lab: [],
      });

      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("No agents online")).toBeInTheDocument();
      });
    });

    it("hides lab section when no labs present", async () => {
      mockApiRequest.mockResolvedValue({
        by_agent: mockResourcesData.by_agent,
        by_lab: [],
      });

      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("By Agent")).toBeInTheDocument();
        expect(screen.queryByText("By Lab")).not.toBeInTheDocument();
      });
    });

    it("shows error state when fetch fails", async () => {
      mockApiRequest.mockRejectedValue(new Error("Network error"));

      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        expect(screen.getByText("Failed to load data")).toBeInTheDocument();
      });
    });
  });

  it("calls onClose when close button is clicked", async () => {
    const user = userEvent.setup();

    render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

    await waitFor(() => {
      expect(screen.getByText("CPU Usage Distribution")).toBeInTheDocument();
    });

    const closeButton = screen.getByRole("button");
    await user.click(closeButton);

    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  describe("resource bars", () => {
    it("renders progress bars for agents", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        // Check for progress bar containers
        const progressBars = document.querySelectorAll(".h-4.bg-stone-200");
        expect(progressBars.length).toBeGreaterThan(0);
      });
    });

    it("renders progress bars for labs", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        // Labs use sage-500 color, but normal CPU agents also use sage-500
        // So we just check there are some sage-500 bars
        const sageBars = document.querySelectorAll(".bg-sage-500");
        expect(sageBars.length).toBeGreaterThanOrEqual(3); // At least 3 labs
      });
    });
  });

  describe("color thresholds", () => {
    it("applies correct colors based on CPU thresholds", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="cpu" />);

      await waitFor(() => {
        // primary-agent: 45.5% (normal - sage)
        // secondary-agent: 75.0% (warning - amber)
        // high-load-agent: 92.5% (danger - red)
        const amberBars = document.querySelectorAll(".bg-amber-500");
        const redBars = document.querySelectorAll(".bg-red-500");

        expect(amberBars.length).toBeGreaterThan(0);
        expect(redBars.length).toBeGreaterThan(0);
      });
    });

    it("applies correct colors based on memory thresholds", async () => {
      render(<ResourcesPopup isOpen={true} onClose={mockOnClose} type="memory" />);

      await waitFor(() => {
        // primary-agent: 62.3% (normal - blue)
        // secondary-agent: 80.0% (warning - amber)
        // high-load-agent: 95.0% (danger - red)
        const amberBars = document.querySelectorAll(".bg-amber-500");
        const redBars = document.querySelectorAll(".bg-red-500");
        const blueBars = document.querySelectorAll(".bg-blue-500");

        expect(blueBars.length).toBeGreaterThan(0);
        expect(amberBars.length).toBeGreaterThan(0);
        expect(redBars.length).toBeGreaterThan(0);
      });
    });
  });
});
