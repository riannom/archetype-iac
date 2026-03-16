import React, { useState } from 'react';
import { Select } from '../../../components/ui/Select';
import type { ScenarioStep, ScenarioStepType } from './scenarioTypes';
import { STEP_TYPE_CONFIG, SCENARIO_STEP_TYPES, createDefaultStep } from './scenarioTypes';
import type { Node, Link } from '../../types';
import LinkStepFields from './stepFields/LinkStepFields';
import NodeStepFields from './stepFields/NodeStepFields';
import WaitStepFields from './stepFields/WaitStepFields';
import ExecStepFields from './stepFields/ExecStepFields';
import VerifyStepFields from './stepFields/VerifyStepFields';

interface StepCardProps {
  step: ScenarioStep;
  index: number;
  total: number;
  nodes: Node[];
  links: Link[];
  linkOptions: string[];
  disabled?: boolean;
  onUpdate: (step: ScenarioStep) => void;
  onRemove: () => void;
  onMove: (direction: -1 | 1) => void;
}

const StepCard: React.FC<StepCardProps> = ({
  step, index, total, nodes, links, linkOptions, disabled, onUpdate, onRemove, onMove,
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const config = STEP_TYPE_CONFIG[step.type];

  const handleTypeChange = (newType: ScenarioStepType) => {
    if (newType === step.type) return;
    const newStep = createDefaultStep(newType);
    newStep.name = step.name;
    onUpdate(newStep);
  };

  const handleFieldUpdate = (patch: Partial<ScenarioStep>) => {
    onUpdate({ ...step, ...patch } as ScenarioStep);
  };

  return (
    <div
      className="mx-3 my-2 rounded-lg border border-stone-200 dark:border-stone-700 bg-stone-50 dark:bg-stone-800/50 overflow-hidden"
      data-testid={`step-card-${index}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-stone-100 dark:bg-stone-800">
        <div className="flex items-center gap-1.5 min-w-0 flex-1">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="p-0.5 text-stone-400 hover:text-stone-600 dark:hover:text-stone-200"
            title={collapsed ? 'Expand' : 'Collapse'}
          >
            <i className={`fa-solid fa-chevron-${collapsed ? 'right' : 'down'} text-[11px]`} />
          </button>
          <span className={`text-[11px] font-black uppercase px-1.5 py-0.5 rounded ${config.color}`}>
            {config.label}
          </span>
          <input
            type="text"
            value={step.name}
            onChange={e => handleFieldUpdate({ name: e.target.value })}
            placeholder="Step name..."
            disabled={disabled}
            className="flex-1 min-w-0 bg-transparent text-xs font-medium text-stone-600 dark:text-stone-300 outline-none placeholder:text-stone-400"
          />
        </div>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button
            onClick={() => onMove(-1)}
            disabled={disabled || index === 0}
            className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-200 disabled:opacity-30 disabled:cursor-not-allowed"
            title="Move up"
          >
            <i className="fa-solid fa-chevron-up text-[11px]" />
          </button>
          <button
            onClick={() => onMove(1)}
            disabled={disabled || index === total - 1}
            className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-200 disabled:opacity-30 disabled:cursor-not-allowed"
            title="Move down"
          >
            <i className="fa-solid fa-chevron-down text-[11px]" />
          </button>
          <button
            onClick={onRemove}
            disabled={disabled}
            className="p-1 text-stone-400 hover:text-red-500 disabled:opacity-30 disabled:cursor-not-allowed"
            title="Remove step"
          >
            <i className="fa-solid fa-times text-[11px]" />
          </button>
        </div>
      </div>

      {/* Body */}
      {!collapsed && (
        <div className="px-3 py-2 space-y-1.5">
          {/* Type selector */}
          <div className="flex items-center gap-2">
            <label className="text-xs text-stone-500 dark:text-stone-400 w-14 flex-shrink-0">Type</label>
            <Select
              value={step.type}
              onChange={e => handleTypeChange(e.target.value as ScenarioStepType)}
              disabled={disabled}
              size="sm"
              className="flex-1"
              options={SCENARIO_STEP_TYPES.map(t => ({
                value: t,
                label: STEP_TYPE_CONFIG[t].label,
              }))}
            />
          </div>

          {/* Type-specific fields */}
          {(step.type === 'link_down' || step.type === 'link_up') && (
            <LinkStepFields step={step} linkOptions={linkOptions} disabled={disabled} onUpdate={handleFieldUpdate} />
          )}
          {(step.type === 'node_stop' || step.type === 'node_start') && (
            <NodeStepFields step={step} nodes={nodes} disabled={disabled} onUpdate={handleFieldUpdate} />
          )}
          {step.type === 'wait' && (
            <WaitStepFields step={step} disabled={disabled} onUpdate={handleFieldUpdate} />
          )}
          {step.type === 'exec' && (
            <ExecStepFields step={step} nodes={nodes} disabled={disabled} onUpdate={handleFieldUpdate} />
          )}
          {step.type === 'verify' && (
            <VerifyStepFields step={step} nodes={nodes} links={links} disabled={disabled} onUpdate={handleFieldUpdate} />
          )}
        </div>
      )}
    </div>
  );
};

export default StepCard;
