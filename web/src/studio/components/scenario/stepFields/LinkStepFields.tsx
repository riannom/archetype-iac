import React from 'react';
import { Select } from '../../../../components/ui/Select';
import type { LinkStep } from '../scenarioTypes';

interface LinkStepFieldsProps {
  step: LinkStep;
  linkOptions: string[];
  disabled?: boolean;
  onUpdate: (patch: Partial<LinkStep>) => void;
}

const LinkStepFields: React.FC<LinkStepFieldsProps> = ({ step, linkOptions, disabled, onUpdate }) => (
  <div className="flex items-center gap-2">
    <label className="text-xs text-stone-500 dark:text-stone-400 w-14 flex-shrink-0">Link</label>
    <Select
      value={step.link}
      onChange={e => onUpdate({ link: e.target.value })}
      disabled={disabled}
      size="sm"
      className="flex-1"
    >
      <option value="">Select link...</option>
      {linkOptions.map(l => <option key={l} value={l}>{l}</option>)}
    </Select>
  </div>
);

export default LinkStepFields;
