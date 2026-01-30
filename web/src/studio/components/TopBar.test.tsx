import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TopBar from "./TopBar";

// Mock theme context
const mockToggleMode = vi.fn();
vi.mock("../../theme/index", () => ({
  useTheme: () => ({
    effectiveMode: "dark",
    toggleMode: mockToggleMode,
  }),
  ThemeSelector: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) =>
    isOpen ? (
      <div data-testid="theme-selector">
        <button onClick={onClose}>Close Theme Selector</button>
      </div>
    ) : null,
}));

// Mock ArchetypeIcon
vi.mock("../../components/icons", () => ({
  ArchetypeIcon: ({ size, className }: { size: number; className: string }) => (
    <svg data-testid="archetype-icon" width={size} className={className} />
  ),
}));

describe("TopBar", () => {
  const defaultProps = {
    labName: "Test Lab",
    onExport: vi.fn(),
    onExit: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("rendering", () => {
    it("renders the component with lab name", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByText("Test Lab")).toBeInTheDocument();
    });

    it("renders ARCHETYPE branding", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByText("ARCHETYPE")).toBeInTheDocument();
      expect(screen.getByText("Network Studio")).toBeInTheDocument();
    });

    it("renders ArchetypeIcon", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByTestId("archetype-icon")).toBeInTheDocument();
    });

    it("renders back button", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByTitle("Back to Dashboard")).toBeInTheDocument();
    });

    it("renders theme toggle button", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByTitle("Switch to light mode")).toBeInTheDocument();
    });

    it("renders palette button for theme settings", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByTitle("Theme Settings")).toBeInTheDocument();
    });

    it("renders export button", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByText("EXPORT")).toBeInTheDocument();
    });

    it("renders logout button", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByText("LOGOUT")).toBeInTheDocument();
    });

    it("shows Lab: prefix before lab name", () => {
      render(<TopBar {...defaultProps} />);
      expect(screen.getByText("Lab:")).toBeInTheDocument();
    });
  });

  describe("navigation", () => {
    it("calls onExit when back button is clicked", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByTitle("Back to Dashboard"));
      expect(defaultProps.onExit).toHaveBeenCalledTimes(1);
    });

    it("calls onExit when logout button is clicked", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByText("LOGOUT"));
      expect(defaultProps.onExit).toHaveBeenCalledTimes(1);
    });
  });

  describe("theme controls", () => {
    it("toggles theme mode when mode button is clicked", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByTitle("Switch to light mode"));
      expect(mockToggleMode).toHaveBeenCalledTimes(1);
    });

    it("opens theme selector when palette button is clicked", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      expect(screen.queryByTestId("theme-selector")).not.toBeInTheDocument();
      await user.click(screen.getByTitle("Theme Settings"));
      expect(screen.getByTestId("theme-selector")).toBeInTheDocument();
    });

    it("closes theme selector when close button is clicked", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByTitle("Theme Settings"));
      expect(screen.getByTestId("theme-selector")).toBeInTheDocument();

      await user.click(screen.getByText("Close Theme Selector"));
      expect(screen.queryByTestId("theme-selector")).not.toBeInTheDocument();
    });
  });

  describe("export dropdown", () => {
    it("shows export dropdown when export button is clicked", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByText("EXPORT"));
      expect(screen.getByText("Export YAML")).toBeInTheDocument();
    });

    it("calls onExport when Export YAML is clicked", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByText("EXPORT"));
      await user.click(screen.getByText("Export YAML"));
      expect(defaultProps.onExport).toHaveBeenCalledTimes(1);
    });

    it("closes dropdown after export", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByText("EXPORT"));
      await user.click(screen.getByText("Export YAML"));

      // Dropdown should be closed
      expect(screen.queryByText("IAC only")).not.toBeInTheDocument();
    });

    it("shows Export Full option when onExportFull is provided", async () => {
      const user = userEvent.setup();
      const onExportFull = vi.fn();
      render(<TopBar {...defaultProps} onExportFull={onExportFull} />);

      await user.click(screen.getByText("EXPORT"));
      expect(screen.getByText("Export Full")).toBeInTheDocument();
    });

    it("does not show Export Full option when onExportFull is not provided", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByText("EXPORT"));
      expect(screen.queryByText("Export Full")).not.toBeInTheDocument();
    });

    it("calls onExportFull when Export Full is clicked", async () => {
      const user = userEvent.setup();
      const onExportFull = vi.fn();
      render(<TopBar {...defaultProps} onExportFull={onExportFull} />);

      await user.click(screen.getByText("EXPORT"));
      await user.click(screen.getByText("Export Full"));
      expect(onExportFull).toHaveBeenCalledTimes(1);
    });

    it("closes dropdown when clicking outside", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByText("EXPORT"));
      expect(screen.getByText("Export YAML")).toBeInTheDocument();

      // Click outside (on the document)
      fireEvent.mouseDown(document.body);
      expect(screen.queryByText("IAC only")).not.toBeInTheDocument();
    });
  });

  describe("lab name editing", () => {
    it("shows edit button when onRename is provided", () => {
      const onRename = vi.fn();
      render(<TopBar {...defaultProps} onRename={onRename} />);

      // The pencil icon should be present (even if visually hidden until hover)
      const labNameButton = screen.getByText("Test Lab");
      expect(labNameButton).toBeInTheDocument();
      expect(labNameButton.closest("button")).toBeInTheDocument();
    });

    it("enters edit mode when lab name is clicked with onRename", async () => {
      const user = userEvent.setup();
      const onRename = vi.fn();
      render(<TopBar {...defaultProps} onRename={onRename} />);

      await user.click(screen.getByText("Test Lab"));
      expect(screen.getByDisplayValue("Test Lab")).toBeInTheDocument();
    });

    it("does not enter edit mode when onRename is not provided", async () => {
      const user = userEvent.setup();
      render(<TopBar {...defaultProps} />);

      await user.click(screen.getByText("Test Lab"));
      expect(screen.queryByDisplayValue("Test Lab")).not.toBeInTheDocument();
    });

    it("calls onRename with new name on Enter", async () => {
      const user = userEvent.setup();
      const onRename = vi.fn();
      render(<TopBar {...defaultProps} onRename={onRename} />);

      await user.click(screen.getByText("Test Lab"));
      const input = screen.getByDisplayValue("Test Lab");
      await user.clear(input);
      await user.type(input, "New Lab Name");
      await user.keyboard("{Enter}");

      expect(onRename).toHaveBeenCalledWith("New Lab Name");
    });

    it("cancels editing on Escape", async () => {
      const user = userEvent.setup();
      const onRename = vi.fn();
      render(<TopBar {...defaultProps} onRename={onRename} />);

      await user.click(screen.getByText("Test Lab"));
      const input = screen.getByDisplayValue("Test Lab");
      await user.clear(input);
      await user.type(input, "Changed Name");
      await user.keyboard("{Escape}");

      expect(onRename).not.toHaveBeenCalled();
      expect(screen.getByText("Test Lab")).toBeInTheDocument();
    });

    it("saves on blur", async () => {
      const user = userEvent.setup();
      const onRename = vi.fn();
      render(<TopBar {...defaultProps} onRename={onRename} />);

      await user.click(screen.getByText("Test Lab"));
      const input = screen.getByDisplayValue("Test Lab");
      await user.clear(input);
      await user.type(input, "Blurred Name");
      fireEvent.blur(input);

      expect(onRename).toHaveBeenCalledWith("Blurred Name");
    });

    it("does not call onRename if name is unchanged", async () => {
      const user = userEvent.setup();
      const onRename = vi.fn();
      render(<TopBar {...defaultProps} onRename={onRename} />);

      await user.click(screen.getByText("Test Lab"));
      const input = screen.getByDisplayValue("Test Lab");
      await user.keyboard("{Enter}");

      expect(onRename).not.toHaveBeenCalled();
    });

    it("does not call onRename for empty name", async () => {
      const user = userEvent.setup();
      const onRename = vi.fn();
      render(<TopBar {...defaultProps} onRename={onRename} />);

      await user.click(screen.getByText("Test Lab"));
      const input = screen.getByDisplayValue("Test Lab");
      await user.clear(input);
      await user.keyboard("{Enter}");

      expect(onRename).not.toHaveBeenCalled();
    });

    it("trims whitespace from name", async () => {
      const user = userEvent.setup();
      const onRename = vi.fn();
      render(<TopBar {...defaultProps} onRename={onRename} />);

      await user.click(screen.getByText("Test Lab"));
      const input = screen.getByDisplayValue("Test Lab");
      await user.clear(input);
      await user.type(input, "  Trimmed Name  ");
      await user.keyboard("{Enter}");

      expect(onRename).toHaveBeenCalledWith("Trimmed Name");
    });

    it("syncs editName when labName prop changes", async () => {
      const user = userEvent.setup();
      const onRename = vi.fn();
      const { rerender } = render(<TopBar {...defaultProps} onRename={onRename} />);

      rerender(<TopBar {...defaultProps} labName="Updated Lab" onRename={onRename} />);
      await user.click(screen.getByText("Updated Lab"));

      expect(screen.getByDisplayValue("Updated Lab")).toBeInTheDocument();
    });
  });
});
