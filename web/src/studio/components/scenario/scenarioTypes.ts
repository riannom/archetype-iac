import type { TestSpec } from '../../types';

export type ScenarioStepType =
  | 'verify'
  | 'link_down'
  | 'link_up'
  | 'node_stop'
  | 'node_start'
  | 'wait'
  | 'exec';

export const SCENARIO_STEP_TYPES: ScenarioStepType[] = [
  'verify', 'link_down', 'link_up', 'node_stop', 'node_start', 'wait', 'exec',
];

let _stepIdCounter = 0;
export function generateStepId(): string {
  return `step-${Date.now()}-${_stepIdCounter++}`;
}

export interface BaseStep {
  id: string;
  type: ScenarioStepType;
  name: string;
}

export interface VerifyStep extends BaseStep {
  type: 'verify';
  specs: TestSpec[];
}

export interface LinkStep extends BaseStep {
  type: 'link_down' | 'link_up';
  link: string; // "node1:iface1 <-> node2:iface2"
}

export interface NodeStep extends BaseStep {
  type: 'node_stop' | 'node_start';
  node: string;
  timeout?: number;
}

export interface WaitStep extends BaseStep {
  type: 'wait';
  seconds: number;
}

export interface ExecStep extends BaseStep {
  type: 'exec';
  node: string;
  cmd: string;
  expect?: string;
}

export type ScenarioStep = VerifyStep | LinkStep | NodeStep | WaitStep | ExecStep;

export interface ScenarioFormState {
  name: string;
  description: string;
  steps: ScenarioStep[];
}

/** Step type display config */
export const STEP_TYPE_CONFIG: Record<ScenarioStepType, { label: string; icon: string; color: string }> = {
  verify: { label: 'Verify', icon: 'fa-check-double', color: 'bg-blue-500/20 text-blue-600 dark:text-blue-400' },
  link_down: { label: 'Link Down', icon: 'fa-link-slash', color: 'bg-red-500/20 text-red-600 dark:text-red-400' },
  link_up: { label: 'Link Up', icon: 'fa-link', color: 'bg-green-500/20 text-green-600 dark:text-green-400' },
  node_stop: { label: 'Stop Node', icon: 'fa-stop', color: 'bg-orange-500/20 text-orange-600 dark:text-orange-400' },
  node_start: { label: 'Start Node', icon: 'fa-play', color: 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400' },
  wait: { label: 'Wait', icon: 'fa-clock', color: 'bg-stone-500/20 text-stone-600 dark:text-stone-400' },
  exec: { label: 'Execute', icon: 'fa-terminal', color: 'bg-purple-500/20 text-purple-600 dark:text-purple-400' },
};

/** Create a default step for a given type */
export function createDefaultStep(type: ScenarioStepType): ScenarioStep {
  const base = { id: generateStepId(), name: '' };
  switch (type) {
    case 'verify':
      return { ...base, type: 'verify', specs: [] };
    case 'link_down':
      return { ...base, type: 'link_down', link: '' };
    case 'link_up':
      return { ...base, type: 'link_up', link: '' };
    case 'node_stop':
      return { ...base, type: 'node_stop', node: '' };
    case 'node_start':
      return { ...base, type: 'node_start', node: '' };
    case 'wait':
      return { ...base, type: 'wait', seconds: 5 };
    case 'exec':
      return { ...base, type: 'exec', node: '', cmd: '' };
  }
}
