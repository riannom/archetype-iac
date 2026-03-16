import React from 'react';
import type { WaitStep } from '../scenarioTypes';

interface WaitStepFieldsProps {
  step: WaitStep;
  disabled?: boolean;
  onUpdate: (patch: Partial<WaitStep>) => void;
}

const WaitStepFields: React.FC<WaitStepFieldsProps> = ({ step, disabled, onUpdate }) => (
  <div className="flex items-center gap-2">
    <label className="text-xs text-stone-500 dark:text-stone-400 w-14 flex-shrink-0">Seconds</label>
    <input
      type="number"
      value={step.seconds}
      onChange={e => onUpdate({ seconds: parseInt(e.target.value) || 1 })}
      min={1}
      max={3600}
      disabled={disabled}
      className="flex-1"
      style={{ maxWidth: 100 }}
    />
  </div>
);

export default WaitStepFields;
