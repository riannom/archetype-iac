import React from 'react';
import { Select } from '../../../../components/ui/Select';
import type { NodeStep } from '../scenarioTypes';
import type { Node } from '../../../types';

interface NodeStepFieldsProps {
  step: NodeStep;
  nodes: Node[];
  disabled?: boolean;
  onUpdate: (patch: Partial<NodeStep>) => void;
}

const NodeStepFields: React.FC<NodeStepFieldsProps> = ({ step, nodes, disabled, onUpdate }) => (
  <>
    <div className="flex items-center gap-2">
      <label className="text-xs text-stone-500 dark:text-stone-400 w-14 flex-shrink-0">Node</label>
      <Select
        value={step.node}
        onChange={e => onUpdate({ node: e.target.value })}
        disabled={disabled}
        size="sm"
        className="flex-1"
      >
        <option value="">Select node...</option>
        {nodes.map(n => <option key={n.id} value={n.name}>{n.name}</option>)}
      </Select>
    </div>
    <div className="flex items-center gap-2">
      <label className="text-xs text-stone-500 dark:text-stone-400 w-14 flex-shrink-0">Timeout</label>
      <input
        type="number"
        value={step.timeout ?? (step.type === 'node_start' ? 120 : 60)}
        onChange={e => {
          const v = parseInt(e.target.value);
          onUpdate({ timeout: (isNaN(v) || v < 1) ? undefined : v });
        }}
        min={1}
        disabled={disabled}
        className="flex-1"
        style={{ maxWidth: 100 }}
      />
      <span className="text-[11px] text-stone-400">seconds</span>
    </div>
  </>
);

export default NodeStepFields;
