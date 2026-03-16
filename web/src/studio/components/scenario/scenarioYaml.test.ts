import { describe, it, expect } from 'vitest';
import { parseScenarioYaml, serializeScenarioYaml } from './scenarioYaml';
import type { ScenarioFormState } from './scenarioTypes';

describe('scenarioYaml', () => {
  describe('parseScenarioYaml', () => {
    it('parses a valid scenario with all step types', () => {
      const yaml = `
name: Full Test
description: Tests all step types
steps:
  - type: verify
    name: Baseline
    specs:
      - type: ping
        source: r1
        target: 10.0.0.1
        count: 3
  - type: link_down
    name: Kill link
    link: "r1:eth1 <-> r2:eth1"
  - type: link_up
    name: Restore link
    link: "r1:eth1 <-> r2:eth1"
  - type: node_stop
    name: Stop router
    node: r1
    timeout: 30
  - type: node_start
    name: Start router
    node: r1
    timeout: 120
  - type: wait
    name: Settle
    seconds: 10
  - type: exec
    name: Check version
    node: r1
    cmd: show version
    expect: "vEOS"
`;
      const result = parseScenarioYaml(yaml);
      expect(result.ok).toBe(true);
      if (!result.ok) return;

      expect(result.state.name).toBe('Full Test');
      expect(result.state.description).toBe('Tests all step types');
      expect(result.state.steps).toHaveLength(7);

      expect(result.state.steps[0].type).toBe('verify');
      expect(result.state.steps[1].type).toBe('link_down');
      expect(result.state.steps[2].type).toBe('link_up');
      expect(result.state.steps[3].type).toBe('node_stop');
      expect(result.state.steps[4].type).toBe('node_start');
      expect(result.state.steps[5].type).toBe('wait');
      expect(result.state.steps[6].type).toBe('exec');

      const verifyStep = result.state.steps[0];
      if (verifyStep.type === 'verify') {
        expect(verifyStep.specs).toHaveLength(1);
        expect(verifyStep.specs[0].type).toBe('ping');
        expect(verifyStep.specs[0].source).toBe('r1');
      }

      const nodeStop = result.state.steps[3];
      if (nodeStop.type === 'node_stop') {
        expect(nodeStop.node).toBe('r1');
        expect(nodeStop.timeout).toBe(30);
      }

      const exec = result.state.steps[6];
      if (exec.type === 'exec') {
        expect(exec.node).toBe('r1');
        expect(exec.cmd).toBe('show version');
        expect(exec.expect).toBe('vEOS');
      }
    });

    it('returns error for invalid YAML', () => {
      const result = parseScenarioYaml('{{invalid');
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.error).toContain('Invalid YAML');
      }
    });

    it('returns error for missing steps', () => {
      const result = parseScenarioYaml('name: Test\n');
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.error).toContain('steps');
      }
    });

    it('returns error for unknown step type', () => {
      const result = parseScenarioYaml('name: Test\nsteps:\n  - type: unknown\n');
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.error).toContain('unknown type');
      }
    });

    it('handles empty steps array', () => {
      const result = parseScenarioYaml('name: Empty\nsteps: []\n');
      expect(result.ok).toBe(true);
      if (result.ok) {
        expect(result.state.steps).toHaveLength(0);
      }
    });

    it('defaults missing optional fields', () => {
      const result = parseScenarioYaml('name: Test\nsteps:\n  - type: wait\n');
      expect(result.ok).toBe(true);
      if (result.ok && result.state.steps[0].type === 'wait') {
        expect(result.state.steps[0].seconds).toBe(5);
        expect(result.state.steps[0].name).toBe('');
      }
    });
  });

  describe('serializeScenarioYaml', () => {
    it('serializes a form state to valid YAML', () => {
      const state: ScenarioFormState = {
        name: 'Test',
        description: 'A test',
        steps: [
          { id: 's1', type: 'wait', name: 'Pause', seconds: 10 },
          { id: 's2', type: 'exec', name: 'Run cmd', node: 'r1', cmd: 'show ip route' },
        ],
      };

      const yaml = serializeScenarioYaml(state);
      expect(yaml).toContain('name: Test');
      expect(yaml).toContain('type: wait');
      expect(yaml).toContain('seconds: 10');
      expect(yaml).toContain('type: exec');
      expect(yaml).toContain('show ip route');
    });
  });

  describe('round-trip', () => {
    it('round-trips all step types', () => {
      const original: ScenarioFormState = {
        name: 'Round Trip',
        description: 'Testing round-trip',
        steps: [
          { id: 's1', type: 'verify', name: 'Check', specs: [{ type: 'ping', source: 'r1', target: '10.0.0.1', count: 3 }] },
          { id: 's2', type: 'link_down', name: 'Down', link: 'r1:eth1 <-> r2:eth1' },
          { id: 's3', type: 'link_up', name: 'Up', link: 'r1:eth1 <-> r2:eth1' },
          { id: 's4', type: 'node_stop', name: 'Stop', node: 'r1', timeout: 30 },
          { id: 's5', type: 'node_start', name: 'Start', node: 'r1', timeout: 120 },
          { id: 's6', type: 'wait', name: 'Wait', seconds: 5 },
          { id: 's7', type: 'exec', name: 'Exec', node: 'r1', cmd: 'show version', expect: 'vEOS' },
        ],
      };

      const yaml = serializeScenarioYaml(original);
      const result = parseScenarioYaml(yaml);
      expect(result.ok).toBe(true);
      if (!result.ok) return;

      expect(result.state.name).toBe(original.name);
      expect(result.state.description).toBe(original.description);
      expect(result.state.steps).toHaveLength(original.steps.length);

      for (let i = 0; i < original.steps.length; i++) {
        expect(result.state.steps[i].type).toBe(original.steps[i].type);
        expect(result.state.steps[i].name).toBe(original.steps[i].name);
      }
    });

    it('preserves exec step expect field through round-trip', () => {
      const state: ScenarioFormState = {
        name: 'Test',
        description: '',
        steps: [{ id: 's1', type: 'exec', name: 'Check', node: 'r1', cmd: 'show ver', expect: 'EOS' }],
      };
      const yaml = serializeScenarioYaml(state);
      const result = parseScenarioYaml(yaml);
      expect(result.ok).toBe(true);
      if (result.ok && result.state.steps[0].type === 'exec') {
        expect(result.state.steps[0].expect).toBe('EOS');
      }
    });
  });
});
