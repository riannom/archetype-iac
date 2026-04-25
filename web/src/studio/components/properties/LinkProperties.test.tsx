import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LinkProperties from './LinkProperties';
import type { PortManager } from '../../hooks/usePortManager';
import type { Link, Node } from '../../types';
import { DeviceType } from '../../types';

const makeNode = (id: string, name: string): Node => ({
  id,
  name,
  x: 0,
  y: 0,
  nodeType: 'device',
  type: DeviceType.ROUTER,
  model: 'ceos',
  version: 'latest',
});

const link: Link = {
  id: 'l1',
  source: 'n1',
  target: 'n2',
  type: 'p2p',
  sourceInterface: 'eth1',
};

const portManager: PortManager = {
  getUsedInterfaces: () => new Set<string>(),
  getAvailableInterfaces: (nodeId: string) =>
    nodeId === 'n1' ? ['eth1', 'eth2', 'eth3'] : ['eth1', 'eth4'],
};

describe('LinkProperties', () => {
  it('shows source and target node names and the trash button', async () => {
    const onDelete = vi.fn();
    const user = userEvent.setup();
    render(
      <LinkProperties
        link={link}
        nodes={[makeNode('n1', 'r1'), makeNode('n2', 'r2')]}
        portManager={portManager}
        onUpdateLink={vi.fn()}
        onDelete={onDelete}
      />,
    );

    expect(screen.getByText('Link Properties')).toBeInTheDocument();
    expect(screen.getAllByText('r1').length).toBeGreaterThan(0);
    expect(screen.getAllByText('r2').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: '' })); // trash icon button
    expect(onDelete).toHaveBeenCalledWith('l1');
  });

  it('emits sourceInterface updates when the source select changes', async () => {
    const onUpdateLink = vi.fn();
    const user = userEvent.setup();
    render(
      <LinkProperties
        link={link}
        nodes={[makeNode('n1', 'r1'), makeNode('n2', 'r2')]}
        portManager={portManager}
        onUpdateLink={onUpdateLink}
        onDelete={vi.fn()}
      />,
    );

    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[0], 'eth2');
    expect(onUpdateLink).toHaveBeenCalledWith('l1', { sourceInterface: 'eth2' });
  });

  it('emits targetInterface updates when the target select changes', async () => {
    const onUpdateLink = vi.fn();
    const user = userEvent.setup();
    render(
      <LinkProperties
        link={link}
        nodes={[makeNode('n1', 'r1'), makeNode('n2', 'r2')]}
        portManager={portManager}
        onUpdateLink={onUpdateLink}
        onDelete={vi.fn()}
      />,
    );

    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[1], 'eth4');
    expect(onUpdateLink).toHaveBeenCalledWith('l1', { targetInterface: 'eth4' });
  });

  it('falls back to empty-string defaults when interfaces are not yet set', () => {
    const linkBare: Link = {
      id: 'l2',
      source: 'n1',
      target: 'n2',
      type: 'p2p',
    };
    render(
      <LinkProperties
        link={linkBare}
        nodes={[makeNode('n1', 'r1'), makeNode('n2', 'r2')]}
        portManager={portManager}
        onUpdateLink={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const selects = screen.getAllByRole('combobox');
    expect(selects[0]).toHaveValue('');
    expect(selects[1]).toHaveValue('');
  });

  it('does not crash when source or target node is missing from the nodes list', () => {
    const orphan: Link = { ...link, source: 'unknown', target: 'also-unknown' };
    expect(() =>
      render(
        <LinkProperties
          link={orphan}
          nodes={[]}
          portManager={portManager}
          onUpdateLink={vi.fn()}
          onDelete={vi.fn()}
        />,
      ),
    ).not.toThrow();
    expect(screen.getByText('Link Properties')).toBeInTheDocument();
  });
});
