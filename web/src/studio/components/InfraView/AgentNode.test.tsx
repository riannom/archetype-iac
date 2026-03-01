import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import AgentNode from "./AgentNode";
import type { AgentGraphNode } from "./types";

function makeAgent(overrides: Partial<AgentGraphNode> = {}): AgentGraphNode {
  return {
    agentId: "agent-1",
    agentName: "Agent One",
    color: "#22c55e",
    nodes: [],
    localLinks: [],
    stats: {
      nodeCount: 2,
      runningCount: 1,
      linkCount: 0,
      vlanTags: new Set<number>(),
    },
    ...overrides,
  };
}

function renderNode(overrides: Partial<React.ComponentProps<typeof AgentNode>> = {}) {
  const props: React.ComponentProps<typeof AgentNode> = {
    agent: makeAgent(),
    x: 100,
    y: 120,
    isSelected: false,
    isDimmed: false,
    onSelect: vi.fn(),
    onDrag: vi.fn(),
    onDragEnd: vi.fn(),
    overflowCount: 0,
    onHoverEnter: vi.fn(),
    onHoverLeave: vi.fn(),
    ...overrides,
  };

  const view = render(
    <svg>
      <AgentNode {...props} />
    </svg>
  );
  const group = view.container.querySelector("g") as SVGGElement;
  return { ...view, props, group };
}

describe("AgentNode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(SVGElement.prototype, "setPointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(SVGElement.prototype, "releasePointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("selects agent on click without drag", () => {
    const { props, group } = renderNode();

    fireEvent.pointerDown(group, { pointerId: 1, clientX: 10, clientY: 10 });
    fireEvent.pointerUp(group, { pointerId: 1, clientX: 10, clientY: 10 });

    expect(props.onSelect).toHaveBeenCalledTimes(1);
    expect(props.onDrag).not.toHaveBeenCalled();
    expect(props.onDragEnd).toHaveBeenCalledTimes(1);
  });

  it("uses multi-select when modifier key is pressed", () => {
    const { props, group } = renderNode();

    fireEvent.pointerDown(group, { pointerId: 1, clientX: 5, clientY: 5 });
    fireEvent.pointerUp(group, { pointerId: 1, clientX: 5, clientY: 5, ctrlKey: true });

    expect(props.onSelect).toHaveBeenCalledTimes(1);
    expect(props.onDrag).not.toHaveBeenCalled();
  });

  it("forwards hover enter/leave callbacks", () => {
    const { props, group } = renderNode();

    fireEvent.pointerEnter(group);
    fireEvent.pointerLeave(group);

    expect(props.onHoverEnter).toHaveBeenCalledTimes(1);
    expect(props.onHoverLeave).toHaveBeenCalledTimes(1);
  });

  it("renders overflow badge when overflow count is present", () => {
    renderNode({ overflowCount: 3 });
    expect(screen.getByText("+3 more")).toBeInTheDocument();
  });
});
