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
  },
  {
    id: "lab-2",
    name: "Production Lab",
    created_at: "2024-01-14T10:00:00Z",
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
  const mockOnRename = vi.fn();

  const defaultProps = {
    labs: mockLabs,
    labStatuses: mockLabStatuses,
    systemMetrics: mockSystemMetrics,
    onSelect: mockOnSelect,
    onCreate: mockOnCreate,
    onDelete: mockOnDelete,
    onRename: mockOnRename,
  };

  beforeEach(() => {
    vi.clearAllMocks();
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

  it("renders lab cards for each lab", () => {
    render(
      <TestWrapper>
        <Dashboard {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Test Lab 1")).toBeInTheDocument();
    expect(screen.getByText("Production Lab")).toBeInTheDocument();
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
        json: () => Promise.resolve({ id: 1, email: "admin@test.com", is_admin: true }),
      });
    });

    afterEach(() => {
      localStorage.removeItem('token');
    });

    it("shows Infrastructure button for admin users", async () => {
      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("Infrastructure")).toBeInTheDocument();
      });
    });

    it("navigates to infrastructure page when Infrastructure is clicked", async () => {
      const user = userEvent.setup();

      render(
        <TestWrapper>
          <Dashboard {...defaultProps} />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByText("Infrastructure")).toBeInTheDocument();
      });

      const infraButton = screen.getByText("Infrastructure").closest("button");
      if (infraButton) {
        await user.click(infraButton);
      }

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
  });
});
