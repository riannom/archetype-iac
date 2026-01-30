import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ContainersPopup from "./ContainersPopup";

// Mock fetch globally
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

const mockContainersData = {
  by_lab: {
    "lab-1": {
      name: "Test Lab 1",
      containers: [
        {
          name: "clab-test-router1",
          status: "running",
          lab_id: "lab-1",
          lab_name: "Test Lab 1",
          node_name: "router1",
          node_kind: "ceos",
          image: "ceos:4.28.0F",
          agent_name: "agent-1",
        },
        {
          name: "clab-test-router2",
          status: "running",
          lab_id: "lab-1",
          lab_name: "Test Lab 1",
          node_name: "router2",
          node_kind: "srlinux",
          image: "srlinux:23.10.1",
          agent_name: "agent-1",
        },
        {
          name: "clab-test-host1",
          status: "stopped",
          lab_id: "lab-1",
          lab_name: "Test Lab 1",
          node_name: "host1",
          node_kind: "linux",
          image: "alpine:latest",
          agent_name: "agent-2",
        },
      ],
    },
    "lab-2": {
      name: "Production Lab",
      containers: [
        {
          name: "clab-prod-spine1",
          status: "running",
          lab_id: "lab-2",
          lab_name: "Production Lab",
          node_name: "spine1",
          node_kind: "ceos",
          image: "ceos:4.29.0F",
          agent_name: "agent-2",
        },
      ],
    },
  },
  system_containers: [
    {
      name: "archetype-api",
      status: "running",
      lab_id: null,
      lab_name: null,
      node_name: null,
      node_kind: null,
      image: "archetype-api:latest",
      agent_name: "agent-1",
    },
    {
      name: "archetype-worker",
      status: "running",
      lab_id: null,
      lab_name: null,
      node_name: null,
      node_kind: null,
      image: "archetype-worker:latest",
      agent_name: "agent-1",
    },
  ],
  total_running: 5,
  total_stopped: 1,
};

describe("ContainersPopup", () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockContainersData),
    });
  });

  it("renders nothing when isOpen is false", () => {
    const { container } = render(
      <ContainersPopup isOpen={false} onClose={mockOnClose} />
    );

    expect(container.firstChild).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("shows loading state when opened", () => {
    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("fetches container data when opened", async () => {
    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith("/api/dashboard/metrics/containers");
    });
  });

  it("displays container summary after loading", async () => {
    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("5 running")).toBeInTheDocument();
      expect(screen.getByText("1 stopped")).toBeInTheDocument();
    });
  });

  it("displays lab sections", async () => {
    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
      expect(screen.getByText("Production Lab")).toBeInTheDocument();
    });
  });

  it("shows container count for each lab", async () => {
    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("3 containers")).toBeInTheDocument();
      expect(screen.getByText("1 container")).toBeInTheDocument();
    });
  });

  it("displays system containers section when present", async () => {
    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("System Containers")).toBeInTheDocument();
      expect(screen.getByText("2 containers")).toBeInTheDocument();
    });
  });

  it("expands lab to show container details when clicked", async () => {
    const user = userEvent.setup();

    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
    });

    // Click to expand lab section
    await user.click(screen.getByText("Test Lab 1"));

    await waitFor(() => {
      expect(screen.getByText("router1")).toBeInTheDocument();
      expect(screen.getByText("router2")).toBeInTheDocument();
      expect(screen.getByText("host1")).toBeInTheDocument();
    });
  });

  it("shows container kind and image", async () => {
    const user = userEvent.setup();

    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Test Lab 1"));

    await waitFor(() => {
      expect(screen.getByText("ceos")).toBeInTheDocument();
      expect(screen.getByText("ceos:4.28.0F")).toBeInTheDocument();
    });
  });

  it("shows running status badge for running containers", async () => {
    const user = userEvent.setup();

    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Test Lab 1"));

    await waitFor(() => {
      const runningBadges = screen.getAllByText("running");
      expect(runningBadges.length).toBeGreaterThan(0);
    });
  });

  it("shows stopped status badge for stopped containers", async () => {
    const user = userEvent.setup();

    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Test Lab 1"));

    await waitFor(() => {
      expect(screen.getByText("stopped")).toBeInTheDocument();
    });
  });

  it("collapses lab section when clicked again", async () => {
    const user = userEvent.setup();

    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
    });

    // Click to expand
    await user.click(screen.getByText("Test Lab 1"));
    await waitFor(() => {
      expect(screen.getByText("router1")).toBeInTheDocument();
    });

    // Click to collapse
    await user.click(screen.getByText("Test Lab 1"));
    await waitFor(() => {
      expect(screen.queryByText("router1")).not.toBeInTheDocument();
    });
  });

  it("expands system containers section when clicked", async () => {
    const user = userEvent.setup();

    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("System Containers")).toBeInTheDocument();
    });

    await user.click(screen.getByText("System Containers"));

    await waitFor(() => {
      expect(screen.getByText("archetype-api")).toBeInTheDocument();
      expect(screen.getByText("archetype-worker")).toBeInTheDocument();
    });
  });

  it("calls onClose when close button is clicked", async () => {
    const user = userEvent.setup();

    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Containers")).toBeInTheDocument();
    });

    // Find the close button (the one with the xmark icon in the header)
    const closeButton = document.querySelector("button.p-1");
    expect(closeButton).toBeInTheDocument();
    if (closeButton) {
      await user.click(closeButton);
    }

    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  it("displays popup title as 'Containers'", async () => {
    render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText("Containers")).toBeInTheDocument();
    });
  });

  describe("host filtering", () => {
    it("displays filtered title when filterHostName is provided", async () => {
      render(
        <ContainersPopup isOpen={true} onClose={mockOnClose} filterHostName="agent-1" />
      );

      await waitFor(() => {
        expect(screen.getByText("Containers on agent-1")).toBeInTheDocument();
      });
    });

    it("filters containers by host name", async () => {
      render(
        <ContainersPopup isOpen={true} onClose={mockOnClose} filterHostName="agent-1" />
      );

      await waitFor(() => {
        // agent-1 has 2 running in lab-1, 2 system containers = 4 total
        // Lab-1 has 2 containers from agent-1 (router1, router2 running)
        // agent-1 should NOT show host1 (agent-2) or lab-2's spine1 (agent-2)
        expect(screen.getByText("4 running")).toBeInTheDocument();
      });
    });

    it("excludes containers from other hosts", async () => {
      const user = userEvent.setup();

      render(
        <ContainersPopup isOpen={true} onClose={mockOnClose} filterHostName="agent-1" />
      );

      await waitFor(() => {
        expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Test Lab 1"));

      // Should show router1 and router2 (agent-1) but not host1 (agent-2)
      await waitFor(() => {
        expect(screen.getByText("router1")).toBeInTheDocument();
        expect(screen.getByText("router2")).toBeInTheDocument();
        expect(screen.queryByText("host1")).not.toBeInTheDocument();
      });
    });

    it("hides labs with no matching containers", async () => {
      render(
        <ContainersPopup isOpen={true} onClose={mockOnClose} filterHostName="agent-1" />
      );

      await waitFor(() => {
        // Production Lab only has agent-2 containers, so should not appear
        expect(screen.queryByText("Production Lab")).not.toBeInTheDocument();
      });
    });
  });

  describe("empty states", () => {
    it("shows empty state when no containers found", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            by_lab: {},
            system_containers: [],
            total_running: 0,
            total_stopped: 0,
          }),
      });

      render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText("No containers found")).toBeInTheDocument();
      });
    });

    it("shows error state when fetch fails", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.reject(new Error("Network error")),
      });

      render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText("Failed to load data")).toBeInTheDocument();
      });
    });
  });

  describe("running count display", () => {
    it("shows running count per lab in header", async () => {
      render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        // Test Lab 1 has 2 running containers
        expect(screen.getByText("2 running")).toBeInTheDocument();
        // Production Lab has 1 running container
        expect(screen.getByText("1 running")).toBeInTheDocument();
      });
    });
  });

  describe("agent name display", () => {
    it("shows agent name for each container", async () => {
      const user = userEvent.setup();

      render(<ContainersPopup isOpen={true} onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
      });

      await user.click(screen.getByText("Test Lab 1"));

      await waitFor(() => {
        expect(screen.getAllByText("agent-1").length).toBeGreaterThan(0);
        expect(screen.getAllByText("agent-2").length).toBeGreaterThan(0);
      });
    });
  });
});
