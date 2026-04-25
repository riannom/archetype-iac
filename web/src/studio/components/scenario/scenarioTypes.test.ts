import { describe, it, expect } from 'vitest';
import {
  SCENARIO_STEP_TYPES,
  STEP_TYPE_CONFIG,
  createDefaultStep,
  generateStepId,
  type ScenarioStepType,
} from './scenarioTypes';

describe('scenarioTypes', () => {
  describe('SCENARIO_STEP_TYPES', () => {
    it('lists every step type that has a config entry', () => {
      const configKeys = Object.keys(STEP_TYPE_CONFIG) as ScenarioStepType[];
      expect(SCENARIO_STEP_TYPES.slice().sort()).toEqual(configKeys.slice().sort());
    });
  });

  describe('STEP_TYPE_CONFIG', () => {
    it('has label/icon/color for every step type', () => {
      for (const t of SCENARIO_STEP_TYPES) {
        const cfg = STEP_TYPE_CONFIG[t];
        expect(cfg.label).toBeTruthy();
        expect(cfg.icon).toMatch(/^fa-/);
        expect(cfg.color).toBeTruthy();
      }
    });
  });

  describe('generateStepId', () => {
    it('returns a string with the step- prefix', () => {
      expect(generateStepId()).toMatch(/^step-\d+-\d+$/);
    });

    it('produces unique ids when called repeatedly', () => {
      const ids = Array.from({ length: 5 }, () => generateStepId());
      expect(new Set(ids).size).toBe(ids.length);
    });
  });

  describe('createDefaultStep', () => {
    it('creates a verify step with empty specs', () => {
      const step = createDefaultStep('verify');
      expect(step.type).toBe('verify');
      expect(step.name).toBe('');
      expect(step.id).toMatch(/^step-/);
      if (step.type === 'verify') {
        expect(step.specs).toEqual([]);
      }
    });

    it('creates a link_down step with empty link', () => {
      const step = createDefaultStep('link_down');
      expect(step.type).toBe('link_down');
      if (step.type === 'link_down' || step.type === 'link_up') {
        expect(step.link).toBe('');
      }
    });

    it('creates a link_up step with empty link', () => {
      const step = createDefaultStep('link_up');
      expect(step.type).toBe('link_up');
      if (step.type === 'link_up' || step.type === 'link_down') {
        expect(step.link).toBe('');
      }
    });

    it('creates a node_stop step with empty node', () => {
      const step = createDefaultStep('node_stop');
      expect(step.type).toBe('node_stop');
      if (step.type === 'node_stop' || step.type === 'node_start') {
        expect(step.node).toBe('');
      }
    });

    it('creates a node_start step with empty node', () => {
      const step = createDefaultStep('node_start');
      expect(step.type).toBe('node_start');
      if (step.type === 'node_start' || step.type === 'node_stop') {
        expect(step.node).toBe('');
      }
    });

    it('creates a wait step with seconds=5', () => {
      const step = createDefaultStep('wait');
      expect(step.type).toBe('wait');
      if (step.type === 'wait') {
        expect(step.seconds).toBe(5);
      }
    });

    it('creates an exec step with empty node and cmd', () => {
      const step = createDefaultStep('exec');
      expect(step.type).toBe('exec');
      if (step.type === 'exec') {
        expect(step.node).toBe('');
        expect(step.cmd).toBe('');
        expect(step.expect).toBeUndefined();
      }
    });

    it('always assigns a fresh id and an empty name', () => {
      const a = createDefaultStep('wait');
      const b = createDefaultStep('wait');
      expect(a.id).not.toBe(b.id);
      expect(a.name).toBe('');
      expect(b.name).toBe('');
    });
  });
});
