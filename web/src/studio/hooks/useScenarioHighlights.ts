import { useMemo } from 'react';
import { ScenarioStepData } from './useLabStateWS';

export interface ScenarioHighlights {
  activeNodeNames: Set<string>;
  activeLinkName: string | null;
  stepName: string;
}

export function useScenarioHighlights(
  activeScenarioJobId: string | null,
  scenarioSteps: ScenarioStepData[],
): ScenarioHighlights | undefined {
  return useMemo(() => {
    if (!activeScenarioJobId) return undefined;
    const runningStep = scenarioSteps.find(s => s.status === 'running' && s.step_index >= 0);
    if (!runningStep || !runningStep.step_data) return undefined;

    const activeNodeNames = new Set<string>();
    let activeLinkName: string | null = null;
    const stepType = runningStep.step_type;
    const sd = runningStep.step_data;

    if (stepType === 'link_down' || stepType === 'link_up') {
      const link = (sd.link as string) || '';
      activeLinkName = link;
      // Extract node names from "node1:iface1 <-> node2:iface2"
      const parts = link.split(' <-> ');
      parts.forEach(p => {
        const nodeName = p.trim().split(':')[0];
        if (nodeName) activeNodeNames.add(nodeName);
      });
    } else if (stepType === 'node_stop' || stepType === 'node_start' || stepType === 'exec') {
      const node = (sd.node as string) || '';
      if (node) activeNodeNames.add(node);
    } else if (stepType === 'verify') {
      const specs = (sd.specs as Array<Record<string, unknown>>) || [];
      specs.forEach(spec => {
        if (spec.source) activeNodeNames.add(spec.source as string);
        if (spec.node) activeNodeNames.add(spec.node as string);
        if (spec.node_name) activeNodeNames.add(spec.node_name as string);
      });
    }

    if (activeNodeNames.size === 0 && !activeLinkName) return undefined;
    return { activeNodeNames, activeLinkName, stepName: runningStep.step_name };
  }, [activeScenarioJobId, scenarioSteps]);
}
