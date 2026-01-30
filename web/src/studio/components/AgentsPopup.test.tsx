import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AgentsPopup from "./AgentsPopup";

// Mock fetch globally
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

const mockAgentsData = [
  {
    id: "agent-1",
    name: "primary-agent",
    address: "192.168.1.10:8080",
    status: "online",
    version: "1.2.3",
    capabilities: {
      providers: ["clab", "docker"],
      features: ["console", "file-transfer"],
      max_concurrent_jobs: 5,
    },
    resource_usage: {
      cpu_percent: 45.5,
      memory_percent: 62.3,
      memory_used_gb: 8.0,
      memory_total_gb: 16.0,
      storage_percent: 55.0,
      storage_used_gb: 110.0,
      storage_total_gb: 200.0,
      containers_running: 10,
      containers_total: 15,
    },
    last_heartbeat: new Date(Date.now() - 30000).toISOString(), // 30 seconds ago
  },
  {
    id: "agent-2",
    name: "secondary-agent",
    address: "192.168.1.11:8080",
    status: "offline",
    version: "1.2.2",
    capabilities: {
      providers: ["clab"],
      features: ["console"],
      max_concurrent_jobs: 3,
    },
    resource_usage: {
      cpu_percent: 0,
      memory_percent: 0,
      memory_used_gb: 0,
      memory_total_gb: 8.0,
      storage_percent: 30.0,
      storage_used_gb: 60.0,
      storage_total_gb: 200.0,
      containers_running: 0,
      containers_total: 5,
    },
    last_heartbeat: new Date(Date.now() - 3600000).toISOString(), // 1 hour ago
  },
  {
    id: "agent-3",
    name: "high-load-agent",
    address: "192.168.1.12:8080",
    status: "online",
    version: "1.2.3",
    capabilities: {
      providers: ["clab"],
      features: [],
    },
    resource_usage: {
      cpu_percent: 85.5,
      memory_percent: 90.2,
      memory_used_gb: 28.8,
      memory_total_gb: 32.0,
      storage_percent: 92.0,
      storage_used_gb: 920.0,
      storage_total_gb: 1000.0,
      containers_running: 25,
      containers_total: 25,
    },
    last_heartbeat: new Date(Date.now() - 5000).toISOString(), // 5 seconds ago
  },
];

describe("AgentsPopup", () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockAgentsData),
    });
  });

  it("renders nothing when isOpen is false", () => {
    const { container } = render(
      <AgentsPopup isOpen={false} onClose={mockOnClose} />
    );

    expect(container.firstChild).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("shows loading state when opened", () => {
    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("fetches agent data when opened", async () => {
    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith("/api/agents/detailed");
    });
  });

  it("displays popup title as 'Agents'", async () => {
    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
  });

  it("displays agent names", async () => {
    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("primary-agent")).toBeInTheDocument();
      expect(screen.getByText("secondary-agent")).toBeInTheDocument();
      expect(screen.getByText("high-load-agent")).toBeInTheDocument();
    });
  });

  it("displays agent addresses", async () => {
    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("192.168.1.10:8080")).toBeInTheDocument();
      expect(screen.getByText("192.168.1.11:8080")).toBeInTheDocument();
      expect(screen.getByText("192.168.1.12:8080")).toBeInTheDocument();
    });
  });

  it("displays online status badge for online agents", async () => {
    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      const onlineBadges = screen.getAllByText("online");
      expect(onlineBadges.length).toBe(2); // primary-agent and high-load-agent
    });
  });

  it("displays offline status badge for offline agents", async () => {
    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("offline")).toBeInTheDocument();
    });
  });

  it("displays agent version", async () => {
    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      // Two agents have v1.2.3 (primary-agent and high-load-agent)
      const versions123 = screen.getAllByText(/v1\.2\.3/);
      expect(versions123.length).toBe(2);
      expect(screen.getByText(/v1\.2\.2/)).toBeInTheDocument();
    });
  });

  describe("capabilities display", () => {
    it("displays provider capabilities", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        const clabBadges = screen.getAllByText("clab");
        expect(clabBadges.length).toBe(3); // all agents have clab
        expect(screen.getByText("docker")).toBeInTheDocument(); // only primary-agent
      });
    });

    it("displays feature capabilities", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        const consoleBadges = screen.getAllByText("console");
        expect(consoleBadges.length).toBe(2); // primary and secondary
        expect(screen.getByText("file-transfer")).toBeInTheDocument(); // only primary
      });
    });
  });

  describe("resource usage display", () => {
    it("displays CPU usage percentage", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        // Each agent shows CPU, so there are 3 CPU labels
        expect(screen.getAllByText("CPU").length).toBe(3);
        expect(screen.getByText("46%")).toBeInTheDocument(); // primary-agent
        expect(screen.getByText("86%")).toBeInTheDocument(); // high-load-agent
      });
    });

    it("displays memory usage percentage", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        // Each agent shows Memory, so there are 3 Memory labels
        expect(screen.getAllByText("Memory").length).toBe(3);
        expect(screen.getByText("62%")).toBeInTheDocument(); // primary-agent
        expect(screen.getByText("90%")).toBeInTheDocument(); // high-load-agent
      });
    });

    it("displays storage usage percentage", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        // Each agent shows Storage, so there are 3 Storage labels
        expect(screen.getAllByText("Storage").length).toBe(3);
        expect(screen.getByText("55%")).toBeInTheDocument(); // primary-agent
        expect(screen.getByText("92%")).toBeInTheDocument(); // high-load-agent
      });
    });

    it("displays memory size information", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        // primary-agent: 8.0/16.0 GB
        expect(screen.getByText(/8\.0 GB\/16\.0 GB/)).toBeInTheDocument();
        // high-load-agent: 28.8/32.0 GB
        expect(screen.getByText(/28\.8 GB\/32\.0 GB/)).toBeInTheDocument();
      });
    });

    it("displays storage size information", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        // primary-agent: 110/200 GB
        expect(screen.getByText(/110\.0 GB\/200\.0 GB/)).toBeInTheDocument();
        // high-load-agent: 920/1000 GB
        expect(screen.getByText(/920\.0 GB\/1000\.0 GB/)).toBeInTheDocument();
      });
    });
  });

  describe("container count display", () => {
    it("displays container counts", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        // primary-agent: 10/15
        expect(screen.getByText("10")).toBeInTheDocument();
        expect(screen.getByText("/15")).toBeInTheDocument();
        // high-load-agent: 25/25
        expect(screen.getByText("25")).toBeInTheDocument();
        expect(screen.getByText("/25")).toBeInTheDocument();
      });
    });

    it("displays 'containers' label", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        const containerLabels = screen.getAllByText("containers");
        expect(containerLabels.length).toBe(3); // one per agent
      });
    });
  });

  it("calls onClose when close button is clicked", async () => {
    const user = userEvent.setup();

    render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });

    // Find the close button (the one with the xmark icon in the header)
    const closeButton = document.querySelector("button.p-1");
    expect(closeButton).toBeInTheDocument();
    if (closeButton) {
      await user.click(closeButton);
    }

    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  describe("empty states", () => {
    it("shows empty state when no agents registered", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve([]),
      });

      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText("No agents registered")).toBeInTheDocument();
      });
    });
  });

  describe("visual indicators", () => {
    it("shows pulsing indicator for online agents", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        const pulsingIndicators = document.querySelectorAll(".animate-pulse");
        expect(pulsingIndicators.length).toBe(2); // 2 online agents
      });
    });

    it("shows green status indicator for online agents", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        const greenIndicators = document.querySelectorAll(".bg-green-500");
        // Status indicators for online agents
        expect(greenIndicators.length).toBeGreaterThan(0);
      });
    });
  });

  describe("timestamp display", () => {
    it("displays last heartbeat time", async () => {
      render(<AgentsPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        // The formatTimestamp function should format these appropriately
        // "30 seconds ago" -> "30s ago", "1 hour ago" -> "1h ago"
        const timestamps = screen.getAllByText(/ago/);
        expect(timestamps.length).toBe(3); // One for each agent
      });
    });
  });
});
