import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ViewTabBar, { type LabView } from './ViewTabBar';

describe('ViewTabBar', () => {
  const baseTabs: LabView[] = ['designer', 'runtime', 'configs', 'logs', 'tests', 'scenarios'];

  it('renders the six base tabs without Infra when showInfraTab is false', () => {
    render(<ViewTabBar view="designer" onViewChange={vi.fn()} showInfraTab={false} />);
    for (const label of ['Designer', 'Runtime', 'Configs', 'Logs', 'Tests', 'Scenarios']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: 'Infra' })).not.toBeInTheDocument();
  });

  it('appends the Infra tab when showInfraTab is true', () => {
    render(<ViewTabBar view="designer" onViewChange={vi.fn()} showInfraTab />);
    expect(screen.getByRole('button', { name: 'Infra' })).toBeInTheDocument();
  });

  it('marks the active tab with the sage classes', () => {
    render(<ViewTabBar view="logs" onViewChange={vi.fn()} showInfraTab={false} />);
    const active = screen.getByRole('button', { name: 'Logs' });
    expect(active.className).toMatch(/text-sage-700/);
    expect(active.className).toMatch(/border-sage-700/);

    const inactive = screen.getByRole('button', { name: 'Designer' });
    expect(inactive.className).not.toMatch(/border-sage-700/);
    expect(inactive.className).toMatch(/border-transparent/);
  });

  it('calls onViewChange with the tab id when a tab is clicked', async () => {
    const onViewChange = vi.fn();
    const user = userEvent.setup();
    render(<ViewTabBar view="designer" onViewChange={onViewChange} showInfraTab />);

    for (const view of [...baseTabs, 'infra' as LabView]) {
      const label = view.charAt(0).toUpperCase() + view.slice(1);
      await user.click(screen.getByRole('button', { name: label }));
      expect(onViewChange).toHaveBeenLastCalledWith(view);
    }
  });
});
