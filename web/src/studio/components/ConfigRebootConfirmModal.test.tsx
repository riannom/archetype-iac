import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConfigRebootConfirmModal from "./ConfigRebootConfirmModal";

vi.mock("../../components/ui/Modal", () => ({
  default: ({
    isOpen,
    onClose,
    title,
    children,
  }: {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
  }) =>
    isOpen ? (
      <div data-testid="modal">
        <h2>{title}</h2>
        <button onClick={onClose}>Close</button>
        {children}
      </div>
    ) : null,
}));

describe("ConfigRebootConfirmModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render when closed", () => {
    render(
      <ConfigRebootConfirmModal
        isOpen={false}
        onClose={vi.fn()}
        onRebootNow={vi.fn()}
        onApplyLater={vi.fn()}
        loading={false}
        actionDescription="Enable secure forwarding."
      />
    );

    expect(screen.queryByTestId("modal")).not.toBeInTheDocument();
  });

  it("renders description and action prompts", () => {
    render(
      <ConfigRebootConfirmModal
        isOpen={true}
        onClose={vi.fn()}
        onRebootNow={vi.fn()}
        onApplyLater={vi.fn()}
        loading={false}
        actionDescription="Enable secure forwarding."
      />
    );

    expect(screen.getByText("Apply Configuration Change")).toBeInTheDocument();
    expect(screen.getByText("Enable secure forwarding.")).toBeInTheDocument();
    expect(screen.getByText("Would you like to reboot the node now to apply this configuration?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reboot Now" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply on Next Boot" })).toBeInTheDocument();
  });

  it("triggers reboot now and apply later callbacks", async () => {
    const user = userEvent.setup();
    const onRebootNow = vi.fn();
    const onApplyLater = vi.fn();

    render(
      <ConfigRebootConfirmModal
        isOpen={true}
        onClose={vi.fn()}
        onRebootNow={onRebootNow}
        onApplyLater={onApplyLater}
        loading={false}
        actionDescription="Apply hostname updates."
      />
    );

    await user.click(screen.getByRole("button", { name: "Reboot Now" }));
    await user.click(screen.getByRole("button", { name: "Apply on Next Boot" }));

    expect(onRebootNow).toHaveBeenCalledTimes(1);
    expect(onApplyLater).toHaveBeenCalledTimes(1);
  });

  it("disables both action buttons while loading", () => {
    render(
      <ConfigRebootConfirmModal
        isOpen={true}
        onClose={vi.fn()}
        onRebootNow={vi.fn()}
        onApplyLater={vi.fn()}
        loading={true}
        actionDescription="Apply hostname updates."
      />
    );

    expect(screen.getByRole("button", { name: "Reboot Now" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Apply on Next Boot" })).toBeDisabled();
  });
});
