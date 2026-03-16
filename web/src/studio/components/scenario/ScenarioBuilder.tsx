import React, { useCallback, useMemo } from 'react';
import StepCard from './StepCard';
import { createDefaultStep } from './scenarioTypes';
import type { ScenarioFormState, ScenarioStep, ScenarioStepType } from './scenarioTypes';
import type { Node, Link } from '../../types';
import { isDeviceNode } from '../../types';

interface ScenarioBuilderProps {
  state: ScenarioFormState;
  onChange: (state: ScenarioFormState) => void;
  nodes: Node[];
  links: Link[];
  disabled?: boolean;
}

const STEP_TEMPLATES: { type: ScenarioStepType; icon: string; label: string }[] = [
  { type: 'verify', icon: 'fa-check-double', label: 'Verify' },
  { type: 'link_down', icon: 'fa-link-slash', label: 'Link Down' },
  { type: 'link_up', icon: 'fa-link', label: 'Link Up' },
  { type: 'node_stop', icon: 'fa-stop', label: 'Stop' },
  { type: 'node_start', icon: 'fa-play', label: 'Start' },
  { type: 'wait', icon: 'fa-clock', label: 'Wait' },
  { type: 'exec', icon: 'fa-terminal', label: 'Exec' },
];

const ScenarioBuilder: React.FC<ScenarioBuilderProps> = ({ state, onChange, nodes, links, disabled }) => {
  const deviceNodes = useMemo(() => (nodes || []).filter(isDeviceNode), [nodes]);

  // Build link options in scenario format: "node1:iface1 <-> node2:iface2"
  const linkOptions = useMemo(() => {
    const nodeMap = new Map((nodes || []).map(n => [n.id, n.name]));
    return (links || []).map(l => {
      const srcName = nodeMap.get(l.source) || l.source;
      const tgtName = nodeMap.get(l.target) || l.target;
      const srcIf = l.sourceInterface || 'eth1';
      const tgtIf = l.targetInterface || 'eth1';
      const [a, b] = [`${srcName}:${srcIf}`, `${tgtName}:${tgtIf}`].sort();
      return `${a} <-> ${b}`;
    });
  }, [nodes, links]);

  const addStep = useCallback((type: ScenarioStepType) => {
    const step = createDefaultStep(type);
    // Pre-fill first node if available
    if ('node' in step && deviceNodes.length > 0) {
      (step as { node: string }).node = deviceNodes[0].name;
    }
    if ('link' in step && linkOptions.length > 0) {
      (step as { link: string }).link = linkOptions[0];
    }
    onChange({ ...state, steps: [...state.steps, step] });
  }, [state, onChange, deviceNodes, linkOptions]);

  const updateStep = useCallback((index: number, step: ScenarioStep) => {
    const steps = state.steps.map((s, i) => (i === index ? step : s));
    onChange({ ...state, steps });
  }, [state, onChange]);

  const removeStep = useCallback((index: number) => {
    onChange({ ...state, steps: state.steps.filter((_, i) => i !== index) });
  }, [state, onChange]);

  const moveStep = useCallback((index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= state.steps.length) return;
    const steps = [...state.steps];
    [steps[index], steps[target]] = [steps[target], steps[index]];
    onChange({ ...state, steps });
  }, [state, onChange]);

  return (
    <div className="flex flex-col h-full">
      {/* Template buttons */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-stone-200 dark:border-stone-700 flex-shrink-0 flex-wrap">
        {STEP_TEMPLATES.map(t => (
          <button
            key={t.type}
            onClick={() => addStep(t.type)}
            disabled={disabled}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-300 hover:bg-sage-100 dark:hover:bg-sage-900/40 hover:text-sage-700 dark:hover:text-sage-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <i className={`fa-solid ${t.icon} text-[11px]`} />
            <span>+{t.label}</span>
          </button>
        ))}
      </div>

      {/* Step list */}
      <div className="flex-1 overflow-y-auto">
        {state.steps.length === 0 && (
          <div className="flex items-center justify-center h-full text-stone-400 text-sm">
            <div className="text-center px-6">
              <i className="fa-solid fa-layer-group text-2xl mb-2 block" />
              <p>Add steps using the buttons above</p>
              <p className="text-xs mt-1">Build a multi-step test workflow</p>
            </div>
          </div>
        )}
        {state.steps.map((step, i) => (
          <StepCard
            key={step.id}
            step={step}
            index={i}
            total={state.steps.length}
            nodes={deviceNodes}
            links={links}
            linkOptions={linkOptions}
            disabled={disabled}
            onUpdate={(s) => updateStep(i, s)}
            onRemove={() => removeStep(i)}
            onMove={(dir) => moveStep(i, dir)}
          />
        ))}
      </div>
    </div>
  );
};

export default ScenarioBuilder;
