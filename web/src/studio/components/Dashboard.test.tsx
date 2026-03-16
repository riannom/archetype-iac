import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Dashboard from "./Dashboard";
import { BrowserRouter } from "react-router-dom";
import { UserProvider } from "../../contexts/UserContext";
import { ThemeProvider } from "../../theme/ThemeProvider";

// Mock FontAwesome
vi.mock("@fortawesome/react-fontawesome", () => ({
  FontAwesomeIcon: () => null,
}));

// Mock fetch for UserProvider
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock VersionBadge
vi.mock("../../components/VersionBadge", () => ({
  VersionBadge: () => <span data-testid="version-badge" />,
}));

// Mock useNotifications to avoid needing NotificationProvider
vi.mock("../../contexts/NotificationContext", () => ({
  useNotifications: () => ({
    notifications: [],
    addNotification: vi.fn(),
    dismissNotification: vi.fn(),
    dismissAllNotifications: vi.fn(),
  }),
}));

// Wrapper component with providers
const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter>
    <ThemeProvider>
      <UserProvider>{children}</UserProvider>
    </ThemeProvider>
  </BrowserRouter>
);

const mockLabs = [
  {
    id: "lab-1",
    name: "Test Lab 1",
    created_at: "2024-01-15T10:00:00Z",
    node_count: 5,
    running_count: 3,
  },
  {
    id: "lab-2",
    name: "Production Lab",
    created_at: "2024-01-14T10:00:00Z",
    node_count: 2,
    running_count: 0,
  },
];

const mockLabStatuses = {
  "lab-1": { running: 3, total: 5 },
  "lab-2": { running: 0, total: 2 },
};

const mockSystemMetrics = {
  agents: { online: 2, total: 3 },
  containers: { running: 10, total: 15 },
  cpu_percent: 45.5,
  memory_percent: 62.3,
  labs_running: 1,
  labs_total: 2,
};

describe("Dashboard", () => {
  const mockOnSelect = vi.fn();
  const mockOnCreate = vi.fn();
  const mockOnDelete = vi.fn();
  const mockOnDownload = vi.fn();
  const mockOnRename = vi.fn();
  const mockOnLogout = vi.fn();

  const defaultProps = {
    labs: mockLabs,
    labStatuses: mockLabStatuses,
    systemMetrics: mockSystemMetrics,
    onSelect: mockOnSelect,
    onDownload: mockOnDownload,
    onCreate: mockOnCreate,
    onDelete: mockOnDelete,
    onRename: mockOnRename,
    onLogout: mockOnLogout,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.pushState({}, "", "/");
    // Mock initial auth check
    mockFetch.mockResolvedValue({
      ok: false,
      status: 401,
    });
  });

  it("renders the dashboard header", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
    expect(screen.getByText("Network Studio")).toBeInTheDocument();
  });

  it("renders the workspace section", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Your Workspace")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Manage, design and deploy your virtual network environments."
      )
    ).toBeInTheDocument();
  });

  it("shows total lab count and list controls", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Total Labs: 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Search labs")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter labs")).toBeInTheDocument();
    expect(screen.getByLabelText("Sort labs")).toBeInTheDocument();
    expect(screen.getByTitle("Previous page")).toBeInTheDocument();
    expect(screen.getByTitle("Next page")).toBeInTheDocument();
  });

  it("renders lab cards for each lab", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
    expect(screen.getByText("Production Lab")).toBeInTheDocument();
  });

  it("filters labs with search input", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    fireEvent.change(screen.getByLabelText("Search labs"), {
      target: { value: "Production" },
    });

    expect(screen.getByText("Production Lab")).toBeInTheDocument();
    expect(screen.queryByText("Test Lab 1")).not.toBeInTheDocument();
  });

  it("filters labs by running/stopped state", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    fireEvent.change(screen.getByLabelText("Filter labs"), {
      target: { value: "running" },
    });

    expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
    expect(screen.queryByText("Production Lab")).not.toBeInTheDocument();
  });

  it("navigates lab pages with back/forward controls", async () => {
    const user = userEvent.setup();
    const manyLabs = Array.from({ length: 10 }, (_, idx) => ({
      id: `lab-${idx + 1}`,
      name: `Lab ${idx + 1}`,
      created_at: `2024-01-${String(idx + 1).padStart(2, "0")}T10:00:00Z`,
      node_count: 1,
      running_count: 0,
    }));

    render(
      <TestWrapper>
        <Dashboard {...defaultProps} labs={manyLabs} />
      </TestWrapper>
    );

    expect(screen.getByText((content) => content.includes("(1/2)"))).toBeInTheDocument();
    expect(screen.queryByText(/^Lab 1$/)).not.toBeInTheDocument();

    await user.click(screen.getByTitle("Next page"));

    expect(screen.getByText((content) => content.includes("(2/2)"))).toBeInTheDocument();
    expect(screen.getByText(/^Lab 1$/)).toBeInTheDocument();
  });

  it("hydrates dashboard controls from URL query params", () => {
    window.history.pushState(
      {},
      "",
      "/?q=Production&status=running&sort=name_desc&page=2"
    );

    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByLabelText("Search labs")).toHaveValue("Production");
    expect(screen.getByLabelText("Filter labs")).toHaveValue("running");
    expect(screen.getByLabelText("Sort labs")).toHaveValue("name_desc");
  });

  it("updates URL query params when changing pages", async () => {
    const user = userEvent.setup();
    const manyLabs = Array.from({ length: 10 }, (_, idx) => ({
      id: `lab-${idx + 1}`,
      name: `Lab ${idx + 1}`,
      created_at: `2024-01-${String(idx + 1).padStart(2, "0")}T10:00:00Z`,
      node_count: 1,
      running_count: 0,
    }));

    render(
      <TestWrapper>
        <Dashboard {...defaultProps} labs={manyLabs} />
      </TestWrapper>
    );

    await user.click(screen.getByTitle("Next page"));
    expect(window.location.search).toContain("page=2");
  });

  it("shows Create New Lab button", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    const createButton = screen.getByRole("button", {
      name: /create new lab/i,
    });
    expect(createButton).toBeInTheDocument();
  });

  it("renders logout button", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByTitle("Logout")).toBeInTheDocument();
  });

  it("calls onLogout when logout button is clicked", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    await user.click(screen.getByTitle("Logout"));
    expect(mockOnLogout).toHaveBeenCalledTimes(1);
  });

  it("calls onCreate when Create New Lab is clicked", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    await user.click(screen.getByRole("button", { name: /create new lab/i }));

    expect(mockOnCreate).toHaveBeenCalledTimes(1);
  });

  it("shows empty state when no labs exist", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} labs={[]} />
      </TestWrapper>
    );

    // Empty state shows "Empty Workspace" heading
    expect(screen.getByText("Empty Workspace")).toBeInTheDocument();
  });

  describe("Lab status display", () => {
    it("shows running indicator for labs with running containers", () => {
      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      // Lab 1 has running containers - status shows count with /total format
      expect(screen.getByText("3")).toBeInTheDocument();
      expect(screen.getByText("/5")).toBeInTheDocument();
    });

    it("shows stopped indicator for labs with no running containers", () => {
      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      // Lab 2 has no running containers - status shows count with /total format
      expect(screen.getByText("0")).toBeInTheDocument();
      expect(screen.getByText("/2")).toBeInTheDocument();
    });
  });

  describe("Theme toggle", () => {
    it("renders theme toggle button", () => {
      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      const themeButton = document.querySelector(".fa-moon, .fa-sun");
      expect(themeButton).toBeInTheDocument();
    });
  });

  describe("Navigation buttons", () => {
    beforeEach(() => {
      // Set token so UserProvider makes the fetch request
      localStorage.setItem('token', 'test-token');
      // Mock authenticated admin user for navigation button tests
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ id: "1", username: "admin", email: "admin@test.com", is_active: true, global_role: "super_admin", created_at: "2024-01-01T00:00:00Z" }),
      });
    });

    afterEach(() => {
      localStorage.removeItem('token');
    });

    it("shows Admin button for admin users", async () => {
      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("Admin")).toBeInTheDocument();
      });
    });

    it("navigates to infrastructure page from admin dropdown", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("Admin")).toBeInTheDocument();
      });

      await user.click(screen.getByTitle("Admin menu"));
      const infraButton = screen.getByTitle("Infrastructure Settings");
      await user.click(infraButton);

      expect(mockNavigate).toHaveBeenCalledWith("/infrastructure");
    });
  });

  describe("Lab card interactions", () => {
    it("shows action buttons on lab cards", () => {
      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      // Look for Open Designer button on lab cards
      const openDesignerButtons = screen.getAllByText("Open Designer");
      expect(openDesignerButtons.length).toBeGreaterThan(0);
    });

    it("calls onDownload when lab card download is clicked", async () => {
      const user = userEvent.setup();
      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      const downloadButtons = screen.getAllByTitle("Download lab bundle");
      await user.click(downloadButtons[0]);
      expect(mockOnDownload).toHaveBeenCalledTimes(1);
      expect(mockOnDownload).toHaveBeenCalledWith(expect.objectContaining({ id: "lab-1" }));
    });
  });
});
