import React from 'react';
import { Select } from '../../../../components/ui/Select';
import type { ExecStep } from '../scenarioTypes';
import type { Node } from '../../../types';

interface ExecStepFieldsProps {
  step: ExecStep;
  nodes: Node[];
  disabled?: boolean;
  onUpdate: (patch: Partial<ExecStep>) => void;
}

const ExecStepFields: React.FC<ExecStepFieldsProps> = ({ step, nodes, disabled, onUpdate }) => (
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
      <label className="text-xs text-stone-500 dark:text-stone-400 w-14 flex-shrink-0">Command</label>
      <input
        type="text"
        value={step.cmd}
        onChange={e => onUpdate({ cmd: e.target.value })}
        placeholder="show version"
        disabled={disabled}
        className="flex-1"
      />
    </div>
    <div className="flex items-center gap-2">
      <label className="text-xs text-stone-500 dark:text-stone-400 w-14 flex-shrink-0">Expect</label>
      <input
        type="text"
        value={step.expect || ''}
        onChange={e => onUpdate({ expect: e.target.value || undefined })}
        placeholder="Regex pattern (optional)"
        disabled={disabled}
        className="flex-1"
      />
    </div>
  </>
);

export default ExecStepFields;
