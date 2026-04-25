import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import VerifyStepFields from './VerifyStepFields';
import type { VerifyStep } from '../scenarioTypes';
import type { Link, Node, TestSpec } from '../../../types';
import { DeviceType } from '../../../types';

vi.mock('../../TestBuilder', () => ({
  __esModule: true,
  default: (props: {
    specs: TestSpec[];
    onUpdateSpecs: (specs: TestSpec[]) => void;
    nodes: Node[];
    links: Link[];
    disabled?: boolean;
  }) => (
    <div data-testid="test-builder-mock">
      <span data-testid="specs-count">{props.specs.length}</span>
      <span data-testid="nodes-count">{props.nodes.length}</span>
      <span data-testid="links-count">{props.links.length}</span>
      <span data-testid="disabled">{String(Boolean(props.disabled))}</span>
      <button
        type="button"
        onClick={() => props.onUpdateSpecs([{ type: 'ping', source: 'r1', target: 'r2' }])}
      >
        emit-spec
      </button>
    </div>
  ),
}));

const node: Node = {
  id: 'n1',
  name: 'r1',
  x: 0,
  y: 0,
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'ceos',
  version: 'latest',
};

const link: Link = {
  id: 'l1',
  source: 'n1',
  target: 'n2',
  type: 'p2p',
};

const baseStep: VerifyStep = {
  id: 'step-1',
  type: 'verify',
  name: 'Verify',
  specs: [],
};

describe('VerifyStepFields', () => {
  it('forwards step specs and topology to TestBuilder', () => {
    render(
      <VerifyStepFields
        step={{ ...baseStep, specs: [{ type: 'ping' }] }}
        nodes={[node]}
        links={[link]}
        onUpdate={vi.fn()}
      />,
    );

    expect(screen.getByTestId('specs-count')).toHaveTextContent('1');
    expect(screen.getByTestId('nodes-count')).toHaveTextContent('1');
    expect(screen.getByTestId('links-count')).toHaveTextContent('1');
    expect(screen.getByTestId('disabled')).toHaveTextContent('false');
  });

  it('propagates disabled prop to TestBuilder', () => {
    render(
      <VerifyStepFields step={baseStep} nodes={[]} links={[]} disabled onUpdate={vi.fn()} />,
    );
    expect(screen.getByTestId('disabled')).toHaveTextContent('true');
  });

  it('routes onUpdateSpecs through onUpdate as a specs patch', async () => {
    const onUpdate = vi.fn();
    const user = (await import('@testing-library/user-event')).default.setup();
    render(
      <VerifyStepFields step={baseStep} nodes={[]} links={[]} onUpdate={onUpdate} />,
    );

    await user.click(screen.getByText('emit-spec'));
    expect(onUpdate).toHaveBeenCalledWith({
      specs: [{ type: 'ping', source: 'r1', target: 'r2' }],
    });
  });
});
