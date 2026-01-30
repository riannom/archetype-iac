import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tabs, TabList, Tab, TabPanel, TabsVariant } from "./Tabs";

function TestTabs({
  activeTab = "tab1",
  onTabChange = vi.fn(),
  variant = "underline" as TabsVariant,
}) {
  return (
    <Tabs activeTab={activeTab} onTabChange={onTabChange} variant={variant}>
      <TabList>
        <Tab id="tab1">Tab 1</Tab>
        <Tab id="tab2">Tab 2</Tab>
        <Tab id="tab3" disabled>
          Tab 3 (Disabled)
        </Tab>
      </TabList>
      <TabPanel id="tab1">Content 1</TabPanel>
      <TabPanel id="tab2">Content 2</TabPanel>
      <TabPanel id="tab3">Content 3</TabPanel>
    </Tabs>
  );
}

describe("Tabs", () => {
  describe("rendering", () => {
    it("renders all tabs", () => {
      render(<TestTabs />);

      expect(screen.getByText("Tab 1")).toBeInTheDocument();
      expect(screen.getByText("Tab 2")).toBeInTheDocument();
      expect(screen.getByText("Tab 3 (Disabled)")).toBeInTheDocument();
    });

    it("renders active tab content", () => {
      render(<TestTabs activeTab="tab1" />);
      expect(screen.getByText("Content 1")).toBeInTheDocument();
    });

    it("hides inactive tab content", () => {
      render(<TestTabs activeTab="tab1" />);
      expect(screen.queryByText("Content 2")).not.toBeInTheDocument();
    });
  });

  describe("variants", () => {
    const variants: TabsVariant[] = ["underline", "pills"];

    variants.forEach((variant) => {
      it(`renders ${variant} variant`, () => {
        render(<TestTabs variant={variant} />);
        expect(screen.getByText("Tab 1")).toBeInTheDocument();
      });
    });

    it("uses underline variant by default", () => {
      render(<TestTabs />);
      expect(screen.getByText("Tab 1")).toBeInTheDocument();
    });
  });

  describe("interaction", () => {
    it("calls onTabChange when tab clicked", async () => {
      const handleChange = vi.fn();
      const user = userEvent.setup();

      render(<TestTabs onTabChange={handleChange} />);

      await user.click(screen.getByText("Tab 2"));

      expect(handleChange).toHaveBeenCalledWith("tab2");
    });

    it("does not call onTabChange for disabled tab", async () => {
      const handleChange = vi.fn();
      const user = userEvent.setup();

      render(<TestTabs onTabChange={handleChange} />);

      await user.click(screen.getByText("Tab 3 (Disabled)"));

      expect(handleChange).not.toHaveBeenCalled();
    });

    it("switches panel content when tab changes", () => {
      const { rerender } = render(<TestTabs activeTab="tab1" />);
      expect(screen.getByText("Content 1")).toBeInTheDocument();

      rerender(<TestTabs activeTab="tab2" />);
      expect(screen.getByText("Content 2")).toBeInTheDocument();
      expect(screen.queryByText("Content 1")).not.toBeInTheDocument();
    });
  });

  describe("disabled state", () => {
    it("renders disabled tab", () => {
      render(<TestTabs />);
      const disabledTab = screen.getByText("Tab 3 (Disabled)");
      expect(disabledTab).toBeDisabled();
    });

    it("applies disabled styling", () => {
      render(<TestTabs />);
      const disabledTab = screen.getByText("Tab 3 (Disabled)");
      expect(disabledTab).toHaveClass("cursor-not-allowed");
    });
  });

  describe("accessibility", () => {
    it("renders tabs as buttons", () => {
      render(<TestTabs />);
      const buttons = screen.getAllByRole("button");
      expect(buttons.length).toBeGreaterThanOrEqual(3);
    });

    it("can focus tabs", () => {
      render(<TestTabs />);
      const tab = screen.getByText("Tab 1");
      tab.focus();
      expect(tab).toHaveFocus();
    });
  });
});

describe("Tab with icon", () => {
  it("renders tab with icon", () => {
    render(
      <Tabs activeTab="tab1" onTabChange={() => {}}>
        <TabList>
          <Tab id="tab1" icon="fa-solid fa-home">
            Home
          </Tab>
        </TabList>
        <TabPanel id="tab1">Home Content</TabPanel>
      </Tabs>
    );

    const icon = document.querySelector("i.fa-home");
    expect(icon).toBeInTheDocument();
  });
});

describe("TabList", () => {
  it("renders children", () => {
    render(
      <Tabs activeTab="tab1" onTabChange={() => {}}>
        <TabList>
          <Tab id="tab1">Tab</Tab>
        </TabList>
        <TabPanel id="tab1">Content</TabPanel>
      </Tabs>
    );

    expect(screen.getByText("Tab")).toBeInTheDocument();
  });

  it("applies custom className", () => {
    render(
      <Tabs activeTab="tab1" onTabChange={() => {}}>
        <TabList className="custom-tablist">
          <Tab id="tab1">Tab</Tab>
        </TabList>
        <TabPanel id="tab1">Content</TabPanel>
      </Tabs>
    );

    const tabList = document.querySelector(".custom-tablist");
    expect(tabList).toBeInTheDocument();
  });
});

describe("TabPanel", () => {
  it("renders content when active", () => {
    render(
      <Tabs activeTab="panel1" onTabChange={() => {}}>
        <TabList>
          <Tab id="panel1">Tab</Tab>
        </TabList>
        <TabPanel id="panel1">Panel Content</TabPanel>
      </Tabs>
    );

    expect(screen.getByText("Panel Content")).toBeInTheDocument();
  });

  it("applies custom className", () => {
    render(
      <Tabs activeTab="panel1" onTabChange={() => {}}>
        <TabList>
          <Tab id="panel1">Tab</Tab>
        </TabList>
        <TabPanel id="panel1" className="custom-panel">
          Content
        </TabPanel>
      </Tabs>
    );

    const panel = document.querySelector(".custom-panel");
    expect(panel).toBeInTheDocument();
  });

  it("does not render when not active", () => {
    render(
      <Tabs activeTab="other" onTabChange={() => {}}>
        <TabList>
          <Tab id="panel1">Tab 1</Tab>
          <Tab id="other">Other</Tab>
        </TabList>
        <TabPanel id="panel1">Hidden Content</TabPanel>
        <TabPanel id="other">Visible</TabPanel>
      </Tabs>
    );

    expect(screen.queryByText("Hidden Content")).not.toBeInTheDocument();
    expect(screen.getByText("Visible")).toBeInTheDocument();
  });
});

describe("error handling", () => {
  it("throws when Tab used outside Tabs", () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => {
      render(<Tab id="orphan">Orphan</Tab>);
    }).toThrow("Tabs components must be used within a Tabs provider");

    consoleSpy.mockRestore();
  });

  it("throws when TabList used outside Tabs", () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => {
      render(
        <TabList>
          <div>Child</div>
        </TabList>
      );
    }).toThrow("Tabs components must be used within a Tabs provider");

    consoleSpy.mockRestore();
  });

  it("throws when TabPanel used outside Tabs", () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => {
      render(<TabPanel id="orphan">Orphan</TabPanel>);
    }).toThrow("Tabs components must be used within a Tabs provider");

    consoleSpy.mockRestore();
  });
});
