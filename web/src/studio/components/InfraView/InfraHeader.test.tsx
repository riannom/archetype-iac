import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { HostGroup } from "./types";
import type { LinkStateData } from "../../hooks/useLabStateWS";
import InfraHeader from "./InfraHeader";

function makeHostGroup(overrides: Partial<HostGroup> = {}): HostGroup {
  return {
    hostId: "h1",
    hostName: "Agent 1",
    agentId: "agent-1",
    nodes: [],
    localLinks: [],
    stats: {
      nodeCount: 0,
      runningCount: 0,
      linkCount: 0,
      vlanTags: new Set<number>(),
    },
    ...overrides,
  };
}

function makeCrossHostLink(overrides: Partial<LinkStateData> = {}): LinkStateData {
  return {
    link_name: "R1:eth1-R2:eth1",
    desired_state: "up",
    actual_state: "up",
    source_node: "R1",
    target_node: "R2",
    ...overrides,
  };
}

describe("InfraHeader", () => {
  it("renders host, node, and link summary counts", () => {
    render(
      <InfraHeader
        hostGroups={[
          makeHostGroup({ localLinks: [makeCrossHostLink()] }),
          makeHostGroup({ hostId: "h2", agentId: "agent-2" }),
        ]}
        crossHostLinks={[makeCrossHostLink(), makeCrossHostLink({ link_name: "R2:eth1-R3:eth1" })]}
        totalNodes={7}
        totalRunning={5}
        allVlanTags={new Set([100, 200, 300, 400])}
      />
    );

    const hostsBadge = screen.getByText("Hosts").closest("div");
    const nodesBadge = screen.getByText("Nodes").closest("div");
    const linksBadge = screen.getByText("Links").closest("div");
    const vlansBadge = screen.getByText("VLANs").closest("div");

    expect(hostsBadge).toHaveTextContent("2");
    expect(nodesBadge).toHaveTextContent("7");
    expect(nodesBadge).toHaveTextContent("(5 running)");
    expect(linksBadge).toHaveTextContent("3"); // links: 2 cross-host + 1 local
    expect(linksBadge).toHaveTextContent("(2 cross-host)");
    expect(vlansBadge).toHaveTextContent("4");
  });

  it("hides cross-host detail when there are no cross-host links", () => {
    render(
      <InfraHeader
        hostGroups={[makeHostGroup({ localLinks: [makeCrossHostLink()] })]}
        crossHostLinks={[]}
        totalNodes={1}
        totalRunning={1}
        allVlanTags={new Set([100])}
      />
    );

    expect(screen.queryByText(/\(.*cross-host\)/)).not.toBeInTheDocument();
  });

  it("hides VLAN stat when vlan tag set is empty", () => {
    render(
      <InfraHeader
        hostGroups={[makeHostGroup()]}
        crossHostLinks={[]}
        totalNodes={0}
        totalRunning={0}
        allVlanTags={new Set()}
      />
    );

    expect(screen.queryByText("VLANs")).not.toBeInTheDocument();
  });
});
