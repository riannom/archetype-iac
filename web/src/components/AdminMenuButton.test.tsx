import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminMenuButton from "./AdminMenuButton";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  user: { id: "u1", username: "admin", global_role: "super_admin", is_active: true },
  canViewInfrastructure: true,
  canManageUsers: true,
  canManageImages: true,
}));

vi.mock("../contexts/UserContext", () => ({
  useUser: () => ({
    user: mocks.user,
    loading: false,
    error: null,
    refreshUser: vi.fn(),
    clearUser: vi.fn(),
  }),
}));

vi.mock("../utils/permissions", () => ({
  canViewInfrastructure: () => mocks.canViewInfrastructure,
  canManageUsers: () => mocks.canManageUsers,
  canManageImages: () => mocks.canManageImages,
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<any>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

describe("AdminMenuButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.canViewInfrastructure = true;
    mocks.canManageUsers = true;
    mocks.canManageImages = true;
  });

  it("does not render for users without infrastructure visibility", () => {
    mocks.canViewInfrastructure = false;

    render(<AdminMenuButton />);

    expect(screen.queryByTitle("Admin menu")).not.toBeInTheDocument();
  });

  it("renders role-based menu items when opened", async () => {
    const user = userEvent.setup();
    render(<AdminMenuButton />);

    await user.click(screen.getByTitle("Admin menu"));

    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Infrastructure")).toBeInTheDocument();
    expect(screen.getByText("Nodes")).toBeInTheDocument();
    expect(screen.getByText("Users")).toBeInTheDocument();
    expect(screen.getByText("Support Bundles")).toBeInTheDocument();
  });

  it("navigates and closes after selecting a menu entry", async () => {
    const user = userEvent.setup();
    render(<AdminMenuButton />);

    await user.click(screen.getByTitle("Admin menu"));
    await user.click(screen.getByText("Support Bundles"));

    expect(mocks.navigate).toHaveBeenCalledWith("/admin/support-bundles");
    expect(screen.queryByText("Support Bundles")).not.toBeInTheDocument();
  });

  it("omits user and node entries when permissions are missing", async () => {
    const user = userEvent.setup();
    mocks.canManageUsers = false;
    mocks.canManageImages = false;

    render(<AdminMenuButton />);
    await user.click(screen.getByTitle("Admin menu"));

    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Infrastructure")).toBeInTheDocument();
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(screen.queryByText("Support Bundles")).not.toBeInTheDocument();
    expect(screen.queryByText("Nodes")).not.toBeInTheDocument();
  });

  it("closes when clicking outside the dropdown", async () => {
    const user = userEvent.setup();
    render(<AdminMenuButton />);

    await user.click(screen.getByTitle("Admin menu"));
    expect(screen.getByText("Settings")).toBeInTheDocument();

    fireEvent.mouseDown(document.body);

    expect(screen.queryByText("Settings")).not.toBeInTheDocument();
  });
});
