import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Dashboard from "./Dashboard";
import { BrowserRouter } from "react-router-dom";
import { UserProvider } from "../../contexts/UserContext";
import { ThemeProvider } from "../../theme/ThemeProvider";

// Mock FontAwesome (Dashboard uses <i className="fa-solid ..."> not FontAwesome component, but keep for safety)
vi.mock("@fortawesome/react-fontawesome", () => ({
  FontAwesomeIcon: () => null,
}));

// Mock fetch for UserProvider
const mockFetch = vi.fn();
(globalThis as any).fetch = mockFetch;

// Mock useNavigate
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

// Mock VersionBadge
vi.mock("../../components/VersionBadge", () => ({
  VersionBadge: () => <span data-testid="version-badge" />,
}));

// Mock useNotifications
vi.mock("../../contexts/NotificationContext", () => ({
  useNotifications: () => ({
    notifications: [],
    addNotification: vi.fn(),
    dismissNotification: vi.fn(),
    dismissAllNotifications: vi.fn(),
  }),
}));

// Mock SystemStatusStrip
vi.mock("./SystemStatusStrip", () => ({
  default: () => <div data-testid="system-status-strip" />,
}));

// Mock SystemLogsModal to capture isOpen prop
vi.mock("./SystemLogsModal", () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) =>
    isOpen ? <div data-testid="system-logs-modal"><button onClick={onClose}>Close Logs</button></div> : null,
}));

// Mock ThemeSelector to capture isOpen prop
const mockThemeSelectorClose = vi.fn();
vi.mock("../../theme/ThemeSelector", () => ({
  ThemeSelector: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) =>
    isOpen ? <div data-testid="theme-selector-modal"><button onClick={onClose}>Close Theme</button></div> : null,
}));

// Mock AdminMenuButton
vi.mock("../../components/AdminMenuButton", () => ({
  default: () => <button data-testid="admin-menu-button">Admin</button>,
}));

const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter>
    <ThemeProvider>
      <UserProvider>{children}</UserProvider>
    </ThemeProvider>
  </BrowserRouter>
);

const makeLab = (overrides: Partial<{
  id: string;
  name: string;
  created_at: string;
  node_count: number;
  running_count: number;
  container_count: number;
  vm_count: number;
}> = {}) => ({
  id: overrides.id ?? "lab-1",
  name: overrides.name ?? "Default Lab",
  created_at: overrides.created_at ?? "2025-06-01T12:00:00Z",
  node_count: overrides.node_count ?? 3,
  running_count: overrides.running_count ?? 0,
  container_count: overrides.container_count ?? 2,
  vm_count: overrides.vm_count ?? 1,
});

const baseProps = () => ({
  labs: [
    makeLab({ id: "lab-a", name: "Alpha Lab", created_at: "2025-06-10T00:00:00Z", node_count: 4, running_count: 2, container_count: 3, vm_count: 1 }),
    makeLab({ id: "lab-b", name: "Beta Lab", created_at: "2025-06-05T00:00:00Z", node_count: 2, running_count: 0, container_count: 2, vm_count: 0 }),
    makeLab({ id: "lab-c", name: "Charlie Lab", created_at: "2025-06-15T00:00:00Z", node_count: 6, running_count: 6, container_count: 4, vm_count: 2 }),
  ],
  labStatuses: {
    "lab-a": { running: 2, total: 4 },
    "lab-b": { running: 0, total: 2 },
    "lab-c": { running: 6, total: 6 },
  },
  systemMetrics: null,
  onSelect: vi.fn(),
  onDownload: vi.fn(),
  onCreate: vi.fn(),
  onDelete: vi.fn(),
  onRename: vi.fn(),
  onLogout: vi.fn(),
});

describe("Dashboard - widgets", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    window.history.pushState({}, "", "/");
    mockFetch.mockResolvedValue({ ok: false, status: 401 });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ---------------------------------------------------------------------------
  // Lab Rename
  // ---------------------------------------------------------------------------
  describe("lab rename", () => {
    it("enters inline edit mode when lab name is clicked", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      // Click on a lab name to start editing
      await user.click(screen.getByText("Alpha Lab"));

      // An input should appear with the lab name pre-filled
      const input = screen.getByDisplayValue("Alpha Lab");
      expect(input).toBeInTheDocument();
      expect(input.tagName).toBe("INPUT");
    });

    it("calls onRename with new name on Enter key", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      await user.click(screen.getByText("Beta Lab"));
      const input = screen.getByDisplayValue("Beta Lab");

      await user.clear(input);
      await user.type(input, "Renamed Lab{Enter}");

      expect(props.onRename).toHaveBeenCalledWith("lab-b", "Renamed Lab");
    });

    it("cancels rename on Escape key without calling onRename", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      await user.click(screen.getByText("Alpha Lab"));
      const input = screen.getByDisplayValue("Alpha Lab");
      await user.clear(input);
      await user.type(input, "Something Else{Escape}");

      expect(props.onRename).not.toHaveBeenCalled();
      // Should exit edit mode — no input visible
      expect(screen.queryByDisplayValue("Something Else")).not.toBeInTheDocument();
    });

    it("saves on blur if name has changed", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      await user.click(screen.getByText("Alpha Lab"));
      const input = screen.getByDisplayValue("Alpha Lab");
      await user.clear(input);
      await user.type(input, "New Name");

      // Blur the input
      fireEvent.blur(input);

      expect(props.onRename).toHaveBeenCalledWith("lab-a", "New Name");
    });

    it("does not call onRename if name is unchanged", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      await user.click(screen.getByText("Alpha Lab"));
      const input = screen.getByDisplayValue("Alpha Lab");
      // Press Enter without changing the name
      await user.type(input, "{Enter}");

      expect(props.onRename).not.toHaveBeenCalled();
    });

    it("does not enter edit mode when onRename is not provided", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();
      // @ts-expect-error - testing undefined callback
      delete props.onRename;

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      await user.click(screen.getByText("Alpha Lab"));
      // Should NOT have an input for editing
      expect(screen.queryByDisplayValue("Alpha Lab")).not.toBeInTheDocument();
    });

    it("trims whitespace from renamed lab name", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      await user.click(screen.getByText("Beta Lab"));
      const input = screen.getByDisplayValue("Beta Lab");
      await user.clear(input);
      await user.type(input, "  Trimmed Name  {Enter}");

      expect(props.onRename).toHaveBeenCalledWith("lab-b", "Trimmed Name");
    });

    it("does not call onRename with empty string", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      await user.click(screen.getByText("Alpha Lab"));
      const input = screen.getByDisplayValue("Alpha Lab");
      await user.clear(input);
      await user.type(input, "{Enter}");

      expect(props.onRename).not.toHaveBeenCalled();
    });
  });

  // ---------------------------------------------------------------------------
  // Pending-Delete State
  // ---------------------------------------------------------------------------
  describe("pending-delete state", () => {
    it("shows confirm/cancel buttons on first delete click", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      // Find the delete buttons — they have title "Delete lab"
      const deleteButtons = screen.getAllByTitle("Delete lab");
      await user.click(deleteButtons[0]);

      // Should now show confirm button (check icon) and cancel button (xmark icon)
      expect(screen.getByTitle("Confirm delete")).toBeInTheDocument();
      expect(screen.getByTitle("Cancel")).toBeInTheDocument();
      // onDelete should NOT have been called yet
      expect(props.onDelete).not.toHaveBeenCalled();
    });

    it("calls onDelete on second click (confirm)", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      const deleteButtons = screen.getAllByTitle("Delete lab");
      await user.click(deleteButtons[0]);

      // Click confirm
      await user.click(screen.getByTitle("Confirm delete"));

      expect(props.onDelete).toHaveBeenCalledTimes(1);
      expect(props.onDelete).toHaveBeenCalledWith("lab-c"); // labs sorted newest first: lab-c first
    });

    it("cancels pending delete when cancel button is clicked", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      const deleteButtons = screen.getAllByTitle("Delete lab");
      await user.click(deleteButtons[0]);
      expect(screen.getByTitle("Confirm delete")).toBeInTheDocument();

      // Click cancel
      await user.click(screen.getByTitle("Cancel"));

      // Confirm/cancel should disappear, Delete lab should reappear
      expect(screen.queryByTitle("Confirm delete")).not.toBeInTheDocument();
      expect(screen.queryByTitle("Cancel")).not.toBeInTheDocument();
      expect(props.onDelete).not.toHaveBeenCalled();
    });

    it("auto-cancels pending delete after 3 second timeout", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      const deleteButtons = screen.getAllByTitle("Delete lab");
      await user.click(deleteButtons[0]);
      expect(screen.getByTitle("Confirm delete")).toBeInTheDocument();

      // Advance timer by 3 seconds (the timeout duration in the component)
      act(() => {
        vi.advanceTimersByTime(3000);
      });

      await waitFor(() => {
        expect(screen.queryByTitle("Confirm delete")).not.toBeInTheDocument();
      });
    });
  });

  // ---------------------------------------------------------------------------
  // Theme Selector
  // ---------------------------------------------------------------------------
  describe("theme selector", () => {
    it("opens theme selector modal when palette button is clicked", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      expect(screen.queryByTestId("theme-selector-modal")).not.toBeInTheDocument();

      await user.click(screen.getByTitle("Theme Settings"));

      expect(screen.getByTestId("theme-selector-modal")).toBeInTheDocument();
    });

    it("closes theme selector modal via onClose callback", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      await user.click(screen.getByTitle("Theme Settings"));
      expect(screen.getByTestId("theme-selector-modal")).toBeInTheDocument();

      await user.click(screen.getByText("Close Theme"));

      expect(screen.queryByTestId("theme-selector-modal")).not.toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Search and Filter
  // ---------------------------------------------------------------------------
  describe("search and filter", () => {
    it("filters labs by search query (case-insensitive)", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      fireEvent.change(screen.getByLabelText("Search labs"), {
        target: { value: "charlie" },
      });

      expect(screen.getByText("Charlie Lab")).toBeInTheDocument();
      expect(screen.queryByText("Alpha Lab")).not.toBeInTheDocument();
      expect(screen.queryByText("Beta Lab")).not.toBeInTheDocument();
    });

    it("shows 'No Matching Labs' when search has no results", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      fireEvent.change(screen.getByLabelText("Search labs"), {
        target: { value: "nonexistent-lab-xyz" },
      });

      expect(screen.getByText("No Matching Labs")).toBeInTheDocument();
      expect(screen.getByText("Adjust your search, filter, or sort options.")).toBeInTheDocument();
    });

    it("filters to running labs only", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      fireEvent.change(screen.getByLabelText("Filter labs"), {
        target: { value: "running" },
      });

      // Alpha (2 running) and Charlie (6 running) should show
      expect(screen.getByText("Alpha Lab")).toBeInTheDocument();
      expect(screen.getByText("Charlie Lab")).toBeInTheDocument();
      // Beta (0 running) should be hidden
      expect(screen.queryByText("Beta Lab")).not.toBeInTheDocument();
    });

    it("filters to stopped labs only", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      fireEvent.change(screen.getByLabelText("Filter labs"), {
        target: { value: "stopped" },
      });

      // Only Beta (0 running) should show
      expect(screen.getByText("Beta Lab")).toBeInTheDocument();
      expect(screen.queryByText("Alpha Lab")).not.toBeInTheDocument();
      expect(screen.queryByText("Charlie Lab")).not.toBeInTheDocument();
    });

    it("sorts labs by name A-Z", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      fireEvent.change(screen.getByLabelText("Sort labs"), {
        target: { value: "name_asc" },
      });

      // Get all "Open Designer" buttons and check the preceding lab name heading order
      const openButtons = screen.getAllByText("Open Designer");
      // Each Open Designer button is inside a lab card — find closest card ancestor and its h3
      const labNames = openButtons.map((btn) => {
        const card = btn.closest("[class*='group']")!;
        return card.querySelector("h3")!.textContent;
      });
      expect(labNames).toEqual(["Alpha Lab", "Beta Lab", "Charlie Lab"]);
    });

    it("sorts labs by name Z-A", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      fireEvent.change(screen.getByLabelText("Sort labs"), {
        target: { value: "name_desc" },
      });

      const openButtons = screen.getAllByText("Open Designer");
      const labNames = openButtons.map((btn) => {
        const card = btn.closest("[class*='group']")!;
        return card.querySelector("h3")!.textContent;
      });
      expect(labNames).toEqual(["Charlie Lab", "Beta Lab", "Alpha Lab"]);
    });

    it("combines search and filter together", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      // Filter to running only
      fireEvent.change(screen.getByLabelText("Filter labs"), {
        target: { value: "running" },
      });

      // Then search for "alpha"
      fireEvent.change(screen.getByLabelText("Search labs"), {
        target: { value: "alpha" },
      });

      expect(screen.getByText("Alpha Lab")).toBeInTheDocument();
      expect(screen.queryByText("Charlie Lab")).not.toBeInTheDocument();
      expect(screen.queryByText("Beta Lab")).not.toBeInTheDocument();
    });

    it("resets page to 1 when search query changes", () => {
      // Start on page 2
      window.history.pushState({}, "", "/?page=2");
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      fireEvent.change(screen.getByLabelText("Search labs"), {
        target: { value: "alpha" },
      });

      // Page param should be removed (defaults to 1)
      expect(window.location.search).not.toContain("page=2");
    });
  });

  // ---------------------------------------------------------------------------
  // Empty State
  // ---------------------------------------------------------------------------
  describe("empty state", () => {
    it("shows 'Empty Workspace' when labs array is empty", () => {
      const props = baseProps();
      props.labs = [];

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      expect(screen.getByText("Empty Workspace")).toBeInTheDocument();
      expect(screen.getByText(/Start your first journey|creating a new network lab/)).toBeInTheDocument();
    });

    it("still shows Create New Lab button when workspace is empty", () => {
      const props = baseProps();
      props.labs = [];

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      const buttons = screen.getAllByRole("button", { name: /create new lab/i });
      expect(buttons.length).toBeGreaterThanOrEqual(1);
    });

    it("shows total labs count as 0 when empty", () => {
      const props = baseProps();
      props.labs = [];

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      expect(screen.getByText("Total Labs: 0")).toBeInTheDocument();
    });

    it("distinguishes between empty workspace and no search results", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      // Search for something that doesn't exist
      fireEvent.change(screen.getByLabelText("Search labs"), {
        target: { value: "zzzzz" },
      });

      // Should show "No Matching Labs", NOT "Empty Workspace"
      expect(screen.getByText("No Matching Labs")).toBeInTheDocument();
      expect(screen.queryByText("Empty Workspace")).not.toBeInTheDocument();
      // Total labs count still shows the actual count
      expect(screen.getByText("Total Labs: 3")).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Loading / Metrics / Misc
  // ---------------------------------------------------------------------------
  describe("loading and status display", () => {
    it("renders lab cards without labStatuses (falls back to running_count)", () => {
      const props = baseProps();
      // Remove labStatuses — component should fall back to lab.running_count
      props.labStatuses = undefined as any;

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      // All three labs should still render
      expect(screen.getByText("Alpha Lab")).toBeInTheDocument();
      expect(screen.getByText("Beta Lab")).toBeInTheDocument();
      expect(screen.getByText("Charlie Lab")).toBeInTheDocument();
    });

    it("displays container and VM count breakdown on lab cards", () => {
      const props = baseProps();
      // Charlie Lab has 4 containers and 2 VMs
      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      expect(screen.getByText(/4 containers/)).toBeInTheDocument();
      expect(screen.getByText(/2 VMs/)).toBeInTheDocument();
    });

    it("does not show container/VM breakdown when counts are zero", () => {
      const props = baseProps();
      props.labs = [makeLab({ id: "lab-z", name: "Zero Lab", node_count: 3, running_count: 0, container_count: 0, vm_count: 0 })];
      props.labStatuses = {};

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      expect(screen.getByText("Zero Lab")).toBeInTheDocument();
      // No container/VM text
      expect(screen.queryByText(/container/)).not.toBeInTheDocument();
      expect(screen.queryByText(/VM/)).not.toBeInTheDocument();
    });

    it("calls onSelect when Open Designer is clicked", async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      const openButtons = screen.getAllByText("Open Designer");
      await user.click(openButtons[0]);

      expect(props.onSelect).toHaveBeenCalledTimes(1);
      expect(props.onSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: expect.any(String) })
      );
    });

    it("shows pagination info for showing range", () => {
      const props = baseProps();

      render(<Wrapper><Dashboard {...props} /></Wrapper>);

      // Showing 1-3 of 3 (1/1)
      expect(screen.getByText((content) => content.includes("1") && content.includes("3") && content.includes("(1/1)"))).toBeInTheDocument();
    });
  });
});
