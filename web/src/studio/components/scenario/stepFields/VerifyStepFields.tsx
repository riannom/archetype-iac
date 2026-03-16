import React from 'react';
import TestBuilder from '../../TestBuilder';
import type { VerifyStep } from '../scenarioTypes';
import type { TestSpec, Node, Link } from '../../../types';

interface VerifyStepFieldsProps {
  step: VerifyStep;
  nodes: Node[];
  links: Link[];
  disabled?: boolean;
  onUpdate: (patch: Partial<VerifyStep>) => void;
}

const VerifyStepFields: React.FC<VerifyStepFieldsProps> = ({ step, nodes, links, disabled, onUpdate }) => (
  <div className="border border-stone-200 dark:border-stone-700 rounded-lg overflow-hidden max-h-80">
    <TestBuilder
      specs={step.specs}
      onUpdateSpecs={(specs: TestSpec[]) => onUpdate({ specs })}
      nodes={nodes}
      links={links}
      disabled={disabled}
    />
  </div>
);

export default VerifyStepFields;
