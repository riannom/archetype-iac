import yaml from 'js-yaml';
import type { ScenarioFormState, ScenarioStep, ScenarioStepType } from './scenarioTypes';
import { generateStepId } from './scenarioTypes';
import type { TestSpec } from '../../types';

const VALID_STEP_TYPES = new Set<string>([
  'verify', 'link_down', 'link_up', 'node_stop', 'node_start', 'wait', 'exec',
]);

export type ParseResult =
  | { ok: true; state: ScenarioFormState }
  | { ok: false; error: string };

/** Parse raw YAML into a ScenarioFormState, or return an error. */
export function parseScenarioYaml(raw: string): ParseResult {
  let doc: unknown;
  try {
    doc = yaml.load(raw);
  } catch (e) {
    return { ok: false, error: `Invalid YAML: ${e instanceof Error ? e.message : String(e)}` };
  }

  if (!doc || typeof doc !== 'object') {
    return { ok: false, error: 'YAML must be a mapping with name and steps' };
  }

  const obj = doc as Record<string, unknown>;
  const name = typeof obj.name === 'string' ? obj.name : '';
  const description = typeof obj.description === 'string' ? obj.description : '';

  if (!Array.isArray(obj.steps)) {
    return { ok: false, error: 'Missing or invalid "steps" array' };
  }

  const steps: ScenarioStep[] = [];
  for (let i = 0; i < obj.steps.length; i++) {
    const rawStep = obj.steps[i];
    if (!rawStep || typeof rawStep !== 'object') {
      return { ok: false, error: `Step ${i + 1}: must be a mapping` };
    }
    const s = rawStep as Record<string, unknown>;
    const stepType = String(s.type || '');
    if (!VALID_STEP_TYPES.has(stepType)) {
      return { ok: false, error: `Step ${i + 1}: unknown type "${stepType}"` };
    }

    const stepName = typeof s.name === 'string' ? s.name : '';

    switch (stepType as ScenarioStepType) {
      case 'verify': {
        const specs = Array.isArray(s.specs) ? s.specs.map(parseTestSpec) : [];
        steps.push({ id: generateStepId(), type: 'verify', name: stepName, specs });
        break;
      }
      case 'link_down':
      case 'link_up':
        steps.push({
          id: generateStepId(),
          type: stepType as 'link_down' | 'link_up',
          name: stepName,
          link: typeof s.link === 'string' ? s.link : '',
        });
        break;
      case 'node_stop':
      case 'node_start':
        steps.push({
          id: generateStepId(),
          type: stepType as 'node_stop' | 'node_start',
          name: stepName,
          node: typeof s.node === 'string' ? s.node : '',
          timeout: typeof s.timeout === 'number' ? s.timeout : undefined,
        });
        break;
      case 'wait':
        steps.push({
          id: generateStepId(),
          type: 'wait',
          name: stepName,
          seconds: typeof s.seconds === 'number' ? s.seconds : 5,
        });
        break;
      case 'exec':
        steps.push({
          id: generateStepId(),
          type: 'exec',
          name: stepName,
          node: typeof s.node === 'string' ? s.node : '',
          cmd: typeof s.cmd === 'string' ? s.cmd : '',
          expect: typeof s.expect === 'string' ? s.expect : undefined,
        });
        break;
    }
  }

  return { ok: true, state: { name, description, steps } };
}

function parseTestSpec(raw: unknown): TestSpec {
  if (!raw || typeof raw !== 'object') return { type: 'ping' };
  const s = raw as Record<string, unknown>;
  const spec: TestSpec = {
    type: (s.type as TestSpec['type']) || 'ping',
  };
  if (typeof s.name === 'string') spec.name = s.name;
  if (typeof s.source === 'string') spec.source = s.source;
  if (typeof s.target === 'string') spec.target = s.target;
  if (typeof s.count === 'number') spec.count = s.count;
  if (typeof s.node === 'string') spec.node = s.node;
  if (typeof s.cmd === 'string') spec.cmd = s.cmd;
  if (typeof s.expect === 'string') spec.expect = s.expect;
  if (typeof s.link_name === 'string') spec.link_name = s.link_name;
  if (typeof s.node_name === 'string') spec.node_name = s.node_name;
  if (typeof s.expected_state === 'string') spec.expected_state = s.expected_state;
  return spec;
}

/** Serialize a ScenarioFormState to YAML string. */
export function serializeScenarioYaml(state: ScenarioFormState): string {
  const doc: Record<string, unknown> = {
    name: state.name,
    description: state.description,
    steps: state.steps.map(serializeStep),
  };
  return yaml.dump(doc, { lineWidth: -1, noRefs: true, quotingType: '"' });
}

function serializeStep(step: ScenarioStep): Record<string, unknown> {
  const base: Record<string, unknown> = { type: step.type, name: step.name };

  switch (step.type) {
    case 'verify':
      base.specs = step.specs.map(serializeTestSpec);
      break;
    case 'link_down':
    case 'link_up':
      base.link = step.link;
      break;
    case 'node_stop':
    case 'node_start':
      base.node = step.node;
      if (step.timeout != null) base.timeout = step.timeout;
      break;
    case 'wait':
      base.seconds = step.seconds;
      break;
    case 'exec':
      base.node = step.node;
      base.cmd = step.cmd;
      if (step.expect) base.expect = step.expect;
      break;
  }

  return base;
}

function serializeTestSpec(spec: TestSpec): Record<string, unknown> {
  const out: Record<string, unknown> = { type: spec.type };
  if (spec.name) out.name = spec.name;
  if (spec.source) out.source = spec.source;
  if (spec.target) out.target = spec.target;
  if (spec.count != null) out.count = spec.count;
  if (spec.node) out.node = spec.node;
  if (spec.cmd) out.cmd = spec.cmd;
  if (spec.expect) out.expect = spec.expect;
  if (spec.link_name) out.link_name = spec.link_name;
  if (spec.node_name) out.node_name = spec.node_name;
  if (spec.expected_state) out.expected_state = spec.expected_state;
  return out;
}
