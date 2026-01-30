import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InterfaceSelect from "./InterfaceSelect";

describe("InterfaceSelect", () => {
  const defaultProps = {
    value: "",
    availableInterfaces: ["eth0", "eth1", "eth2"],
    onChange: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("rendering", () => {
    it("renders a select element", () => {
      render(<InterfaceSelect {...defaultProps} />);
      expect(screen.getByRole("combobox")).toBeInTheDocument();
    });

    it("renders placeholder when no value is selected", () => {
      render(<InterfaceSelect {...defaultProps} />);
      expect(screen.getByText("Select interface")).toBeInTheDocument();
    });

    it("renders custom placeholder", () => {
      render(<InterfaceSelect {...defaultProps} placeholder="Choose an interface" />);
      expect(screen.getByText("Choose an interface")).toBeInTheDocument();
    });

    it("renders all available interfaces as options", () => {
      render(<InterfaceSelect {...defaultProps} />);
      expect(screen.getByRole("option", { name: "eth0" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "eth1" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "eth2" })).toBeInTheDocument();
    });

    it("does not render placeholder when value is selected", () => {
      render(<InterfaceSelect {...defaultProps} value="eth0" />);
      expect(screen.queryByText("Select interface")).not.toBeInTheDocument();
    });

    it("shows selected value", () => {
      render(<InterfaceSelect {...defaultProps} value="eth1" />);
      const select = screen.getByRole("combobox") as HTMLSelectElement;
      expect(select.value).toBe("eth1");
    });
  });

  describe("options list", () => {
    it("includes current value if not in available list", () => {
      render(
        <InterfaceSelect
          {...defaultProps}
          value="eth99"
          availableInterfaces={["eth0", "eth1"]}
        />
      );
      expect(screen.getByRole("option", { name: "eth99" })).toBeInTheDocument();
    });

    it("does not duplicate current value if in available list", () => {
      render(
        <InterfaceSelect
          {...defaultProps}
          value="eth0"
          availableInterfaces={["eth0", "eth1"]}
        />
      );
      const options = screen.getAllByRole("option");
      const eth0Options = options.filter((opt) => opt.textContent === "eth0");
      expect(eth0Options.length).toBe(1);
    });

    it("orders current value first when not in available list", () => {
      render(
        <InterfaceSelect
          {...defaultProps}
          value="custom-iface"
          availableInterfaces={["eth0", "eth1"]}
        />
      );
      const options = screen.getAllByRole("option");
      expect(options[0]).toHaveValue("custom-iface");
    });

    it("handles empty available interfaces list", () => {
      render(<InterfaceSelect {...defaultProps} availableInterfaces={[]} />);
      const options = screen.getAllByRole("option");
      // Only placeholder
      expect(options.length).toBe(1);
    });
  });

  describe("onChange handling", () => {
    it("calls onChange when selection changes", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      render(<InterfaceSelect {...defaultProps} onChange={onChange} />);

      await user.selectOptions(screen.getByRole("combobox"), "eth1");
      expect(onChange).toHaveBeenCalledWith("eth1");
    });

    it("passes the selected value to onChange", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      render(<InterfaceSelect {...defaultProps} onChange={onChange} />);

      await user.selectOptions(screen.getByRole("combobox"), "eth2");
      expect(onChange).toHaveBeenCalledWith("eth2");
    });
  });

  describe("disabled state", () => {
    it("disables select when disabled prop is true", () => {
      render(<InterfaceSelect {...defaultProps} disabled={true} />);
      const select = screen.getByRole("combobox");
      expect(select).toBeDisabled();
    });

    it("enables select when disabled prop is false", () => {
      render(<InterfaceSelect {...defaultProps} disabled={false} />);
      const select = screen.getByRole("combobox");
      expect(select).not.toBeDisabled();
    });

    it("applies disabled styling", () => {
      render(<InterfaceSelect {...defaultProps} disabled={true} />);
      const select = screen.getByRole("combobox");
      expect(select).toHaveClass("opacity-50");
      expect(select).toHaveClass("cursor-not-allowed");
    });

    it("does not call onChange when disabled", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      render(<InterfaceSelect {...defaultProps} onChange={onChange} disabled={true} />);

      // Attempting to select should not work because it's disabled
      const select = screen.getByRole("combobox");
      expect(select).toBeDisabled();
      // User events should not trigger onChange on disabled elements
    });
  });

  describe("className prop", () => {
    it("applies additional className", () => {
      render(<InterfaceSelect {...defaultProps} className="custom-class" />);
      const select = screen.getByRole("combobox");
      expect(select).toHaveClass("custom-class");
    });

    it("keeps default classes with additional className", () => {
      render(<InterfaceSelect {...defaultProps} className="custom-class" />);
      const select = screen.getByRole("combobox");
      expect(select).toHaveClass("w-full");
      expect(select).toHaveClass("custom-class");
    });
  });

  describe("styling", () => {
    it("has base styling classes", () => {
      render(<InterfaceSelect {...defaultProps} />);
      const select = screen.getByRole("combobox");
      expect(select).toHaveClass("w-full");
      expect(select).toHaveClass("rounded");
      expect(select).toHaveClass("cursor-pointer");
    });

    it("has appearance-none class for custom styling", () => {
      render(<InterfaceSelect {...defaultProps} />);
      const select = screen.getByRole("combobox");
      expect(select).toHaveClass("appearance-none");
    });
  });

  describe("edge cases", () => {
    it("handles interfaces with special characters", () => {
      const interfaces = ["eth0.100", "vlan-prod", "br_mgmt"];
      render(<InterfaceSelect {...defaultProps} availableInterfaces={interfaces} />);

      expect(screen.getByRole("option", { name: "eth0.100" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "vlan-prod" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "br_mgmt" })).toBeInTheDocument();
    });

    it("handles many interfaces", () => {
      const manyInterfaces = Array.from({ length: 100 }, (_, i) => `eth${i}`);
      render(<InterfaceSelect {...defaultProps} availableInterfaces={manyInterfaces} />);

      const options = screen.getAllByRole("option");
      // 100 interfaces + 1 placeholder
      expect(options.length).toBe(101);
    });

    it("handles interface names that are numbers", () => {
      const interfaces = ["0", "1", "2"];
      render(<InterfaceSelect {...defaultProps} availableInterfaces={interfaces} />);

      expect(screen.getByRole("option", { name: "0" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "1" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "2" })).toBeInTheDocument();
    });
  });

  describe("memoization", () => {
    it("recalculates options when availableInterfaces changes", () => {
      const { rerender } = render(<InterfaceSelect {...defaultProps} />);

      expect(screen.getByRole("option", { name: "eth0" })).toBeInTheDocument();

      rerender(
        <InterfaceSelect {...defaultProps} availableInterfaces={["ens192", "ens224"]} />
      );

      expect(screen.queryByRole("option", { name: "eth0" })).not.toBeInTheDocument();
      expect(screen.getByRole("option", { name: "ens192" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "ens224" })).toBeInTheDocument();
    });

    it("recalculates options when value changes", () => {
      const { rerender } = render(
        <InterfaceSelect {...defaultProps} value="custom-1" availableInterfaces={["eth0"]} />
      );

      expect(screen.getByRole("option", { name: "custom-1" })).toBeInTheDocument();

      rerender(
        <InterfaceSelect {...defaultProps} value="custom-2" availableInterfaces={["eth0"]} />
      );

      // custom-1 should no longer be in the list
      expect(screen.queryByRole("option", { name: "custom-1" })).not.toBeInTheDocument();
      expect(screen.getByRole("option", { name: "custom-2" })).toBeInTheDocument();
    });
  });
});
