import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LinkStepFields from './LinkStepFields';
import type { LinkStep } from '../scenarioTypes';

const baseStep: LinkStep = {
  id: 'step-1',
  type: 'link_down',
  name: 'Drop link',
  link: '',
};

describe('LinkStepFields', () => {
  it('renders the placeholder option and provided link options', () => {
    render(
      <LinkStepFields
        step={baseStep}
        linkOptions={['r1:eth1 <-> r2:eth1', 'r1:eth2 <-> r3:eth1']}
        onUpdate={vi.fn()}
      />,
    );
    expect(screen.getByText('Select link...')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'r1:eth1 <-> r2:eth1' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'r1:eth2 <-> r3:eth1' })).toBeInTheDocument();
  });

  it('reflects the current link value', () => {
    render(
      <LinkStepFields
        step={{ ...baseStep, link: 'r1:eth1 <-> r2:eth1' }}
        linkOptions={['r1:eth1 <-> r2:eth1']}
        onUpdate={vi.fn()}
      />,
    );
    expect(screen.getByRole('combobox')).toHaveValue('r1:eth1 <-> r2:eth1');
  });

  it('calls onUpdate when a link is selected', async () => {
    const onUpdate = vi.fn();
    const user = userEvent.setup();
    render(
      <LinkStepFields
        step={baseStep}
        linkOptions={['r1:eth1 <-> r2:eth1']}
        onUpdate={onUpdate}
      />,
    );

    await user.selectOptions(screen.getByRole('combobox'), 'r1:eth1 <-> r2:eth1');
    expect(onUpdate).toHaveBeenCalledWith({ link: 'r1:eth1 <-> r2:eth1' });
  });

  it('disables the selector when disabled prop is set', () => {
    render(
      <LinkStepFields step={baseStep} linkOptions={['x']} disabled onUpdate={vi.fn()} />,
    );
    expect(screen.getByRole('combobox')).toBeDisabled();
  });

  it('renders only the placeholder when there are no links', () => {
    render(<LinkStepFields step={baseStep} linkOptions={[]} onUpdate={vi.fn()} />);
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent('Select link...');
  });
});
