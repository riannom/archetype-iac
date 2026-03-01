import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import DetailPanel from "./DetailPanel";
import type { AgentGraphNode, CrossHostBundle } from "./types";

vi.mock("./AgentDetailView", () => ({
  default: ({ agent }: { agent: AgentGraphNode }) => (
    <div data-testid="agent-detail">agent:{agent.agentName}</div>
  ),
}));

vi.mock("./TunnelDetailView", () => ({
  default: ({
    selectedAgentNodes,
    relevantBundles,
  }: {
    selectedAgentNodes: AgentGraphNode[];
    relevantBundles: CrossHostBundle[];
  }) => (
    <div data-testid="tunnel-detail">
      agents:{selectedAgentNodes.length} bundles:{relevantBundles.length}
    </div>
  ),
}));

function makeAgent(overrides: Partial<AgentGraphNode> = {}): AgentGraphNode {
  return {
    agentId: "a1",
    agentName: "Agent 1",
    color: "#22c55e",
    nodes: [],
    localLinks: [],
    stats: {
      nodeCount: 4,
      runningCount: 3,
      linkCount: 2,
      vlanTags: new Set<number>(),
    },
    ...overrides,
  };
}

function makeBundle(overrides: Partial<CrossHostBundle> = {}): CrossHostBundle {
  return {
    agentA: "a1",
    agentB: "a2",
    links: [],
    hasError: false,
    allUp: true,
    ...overrides,
  };
}

describe("DetailPanel", () => {
  it("stays collapsed with no selection", () => {
    const { container } = render(
      <DetailPanel
        selectedIds={new Set()}
        agentNodes={[makeAgent()]}
        crossHostBundles={[]}
        onClose={vi.fn()}
      />
    );

    const panel = container.firstElementChild as HTMLElement;
    expect(panel.style.maxHeight).toBe("0px");
    expect(screen.queryByTestId("agent-detail")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tunnel-detail")).not.toBeInTheDocument();
  });

  it("renders agent detail for a single selected agent", () => {
    const onClose = vi.fn();
    render(
      <DetailPanel
        selectedIds={new Set(["a1"])}
        agentNodes={[makeAgent({ agentId: "a1", agentName: "Agent 1" })]}
        crossHostBundles={[]}
        onClose={onClose}
      />
    );

    expect(screen.getByTestId("agent-detail")).toHaveTextContent("agent:Agent 1");
    fireEvent.click(screen.getByTitle("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders tunnel detail for multi-agent selection and filters bundles", () => {
    render(
      <DetailPanel
        selectedIds={new Set(["a1", "a2"])}
        agentNodes={[
          makeAgent({ agentId: "a1", agentName: "Agent 1" }),
          makeAgent({ agentId: "a2", agentName: "Agent 2", color: "#3b82f6" }),
          makeAgent({ agentId: "a3", agentName: "Agent 3", color: "#f97316" }),
        ]}
        crossHostBundles={[
          makeBundle({ agentA: "a1", agentB: "a2" }),
          makeBundle({ agentA: "a1", agentB: "a3" }),
        ]}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByTestId("tunnel-detail")).toHaveTextContent("agents:2 bundles:1");
  });
});
