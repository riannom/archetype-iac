import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CreateScenarioModal from './CreateScenarioModal';

describe('CreateScenarioModal', () => {
  it('does not render when closed', () => {
    render(<CreateScenarioModal isOpen={false} onClose={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.queryByText('New Scenario')).not.toBeInTheDocument();
  });

  it('renders title when open', () => {
    render(<CreateScenarioModal isOpen={true} onClose={vi.fn()} onCreate={vi.fn()} />);
    expect(screen.getByText('New Scenario')).toBeInTheDocument();
  });

  it('calls onCreate with filename on submit', async () => {
    const onCreate = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();

    render(<CreateScenarioModal isOpen={true} onClose={onClose} onCreate={onCreate} />);

    await user.type(screen.getByPlaceholderText('failover_test.yml'), 'my_test');
    await user.click(screen.getByText('Create'));

    expect(onCreate).toHaveBeenCalledWith('my_test.yml');
    expect(onClose).toHaveBeenCalled();
  });

  it('appends .yml if missing', async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();

    render(<CreateScenarioModal isOpen={true} onClose={vi.fn()} onCreate={onCreate} />);

    await user.type(screen.getByPlaceholderText('failover_test.yml'), 'basic');
    await user.click(screen.getByText('Create'));

    expect(onCreate).toHaveBeenCalledWith('basic.yml');
  });

  it('does not append .yml if already present', async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();

    render(<CreateScenarioModal isOpen={true} onClose={vi.fn()} onCreate={onCreate} />);

    await user.type(screen.getByPlaceholderText('failover_test.yml'), 'test.yml');
    await user.click(screen.getByText('Create'));

    expect(onCreate).toHaveBeenCalledWith('test.yml');
  });

  it('shows error for empty filename', async () => {
    const user = userEvent.setup();
    render(<CreateScenarioModal isOpen={true} onClose={vi.fn()} onCreate={vi.fn()} />);

    await user.click(screen.getByText('Create'));

    expect(screen.getByText('Filename is required')).toBeInTheDocument();
  });

  it('shows the regex error for filenames with disallowed characters', async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    render(<CreateScenarioModal isOpen={true} onClose={vi.fn()} onCreate={onCreate} />);

    // Leading hyphen is rejected by the [A-Za-z0-9_] start anchor
    await user.type(screen.getByPlaceholderText('failover_test.yml'), '-bad name!');
    await user.click(screen.getByText('Create'));

    expect(
      screen.getByText('Only letters, numbers, hyphens, underscores, and dots allowed'),
    ).toBeInTheDocument();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('calls onClose when Cancel clicked', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();

    render(<CreateScenarioModal isOpen={true} onClose={onClose} onCreate={vi.fn()} />);

    await user.click(screen.getByText('Cancel'));
    expect(onClose).toHaveBeenCalled();
  });
});
