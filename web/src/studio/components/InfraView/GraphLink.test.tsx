import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import GraphLink from "./GraphLink";
import type { LinkStateData } from "../../hooks/useLabStateWS";

function makeLinkState(overrides: Partial<LinkStateData> = {}): LinkStateData {
  return {
    link_name: "R1:eth1-R2:eth1",
    desired_state: "up",
    actual_state: "up",
    source_node: "R1",
    target_node: "R2",
    ...overrides,
  };
}

function renderGraphLink(overrides: Partial<React.ComponentProps<typeof GraphLink>> = {}) {
  const props: React.ComponentProps<typeof GraphLink> = {
    linkState: makeLinkState(),
    x1: 10,
    y1: 20,
    x2: 110,
    y2: 120,
    isHighlighted: false,
    isDimmed: false,
    isSelected: false,
    onSelect: vi.fn(),
    ...overrides,
  };

  const view = render(
    <svg>
      <GraphLink {...props} />
    </svg>
  );
  return { ...view, props };
}

describe("GraphLink", () => {
  it("calls onSelect when hit area is clicked", () => {
    const { container, props } = renderGraphLink();
    const hitArea = container.querySelector('line[stroke="transparent"]') as SVGLineElement;
    fireEvent.click(hitArea);
    expect(props.onSelect).toHaveBeenCalledWith("R1:eth1-R2:eth1");
  });

  it("renders VNI badge and selects link from badge click", () => {
    const { container, props } = renderGraphLink({
      linkState: makeLinkState({ vni: 10301 }),
    });

    expect(screen.getByText("VNI 10301")).toBeInTheDocument();

    const badge = container.querySelector("rect") as SVGRectElement;
    fireEvent.click(badge);
    expect(props.onSelect).toHaveBeenCalledWith("R1:eth1-R2:eth1");
  });

  it("omits VNI badge when no VNI is set", () => {
    renderGraphLink({ linkState: makeLinkState({ vni: null }) });
    expect(screen.queryByText(/VNI/)).not.toBeInTheDocument();
  });

  it("uses fallback color for unknown link state", () => {
    const { container } = renderGraphLink({
      linkState: makeLinkState({ actual_state: "mystery" as any }),
    });

    const visibleLine = container.querySelector('line[stroke="#57534e"]');
    expect(visibleLine).toBeInTheDocument();
  });

  it("applies selected styling and full opacity when selected", () => {
    const { container } = renderGraphLink({
      isSelected: true,
      isDimmed: true,
    });

    const rootGroup = container.querySelector("g");
    const visibleLine = container.querySelector('line[stroke-dasharray="4 3"]');
    const selectionGlow = container.querySelector('line[opacity="0.2"]');

    expect(rootGroup).toHaveAttribute("opacity", "1");
    expect(visibleLine).toHaveAttribute("stroke-width", "2");
    expect(selectionGlow).toBeInTheDocument();
  });

  it("uses 0.7 opacity when highlighted but not dimmed or selected", () => {
    const { container } = renderGraphLink({
      isHighlighted: true,
      isDimmed: false,
      isSelected: false,
    });

    const rootGroup = container.querySelector("g");
    expect(rootGroup).toHaveAttribute("opacity", "0.7");
  });

  it("uses 0.12 opacity when dimmed and not selected", () => {
    const { container } = renderGraphLink({
      isDimmed: true,
      isSelected: false,
      isHighlighted: false,
    });

    const rootGroup = container.querySelector("g");
    expect(rootGroup).toHaveAttribute("opacity", "0.12");
  });

  it("renders with default cursor on hit area when onSelect is not provided", () => {
    const { container } = renderGraphLink({ onSelect: undefined });
    const hitArea = container.querySelector('line[stroke="transparent"]') as SVGLineElement;
    expect(hitArea.style.cursor).toBe("default");
  });

  it("clicking the hit area without onSelect does not throw", () => {
    const { container } = renderGraphLink({ onSelect: undefined });
    const hitArea = container.querySelector('line[stroke="transparent"]') as SVGLineElement;
    expect(() => fireEvent.click(hitArea)).not.toThrow();
  });

  it("badge wrapper uses default cursor when onSelect is not provided", () => {
    const { container } = renderGraphLink({
      onSelect: undefined,
      linkState: makeLinkState({ vni: 42 }),
    });
    const badgeWrapper = container.querySelectorAll("g")[1] as SVGGElement;
    expect(badgeWrapper.style.cursor).toBe("default");
  });

  it("uses the brighter VNI badge fill when selected", () => {
    const { container } = renderGraphLink({
      isSelected: true,
      linkState: makeLinkState({ vni: 99 }),
    });
    const rect = container.querySelector("rect") as SVGRectElement;
    expect(rect.getAttribute("fill")).toBe("rgba(99, 102, 241, 1)");
  });

  it("uses the dimmer VNI badge fill when not selected", () => {
    const { container } = renderGraphLink({
      isSelected: false,
      linkState: makeLinkState({ vni: 7 }),
    });
    const rect = container.querySelector("rect") as SVGRectElement;
    expect(rect.getAttribute("fill")).toBe("rgba(99, 102, 241, 0.85)");
  });
});
