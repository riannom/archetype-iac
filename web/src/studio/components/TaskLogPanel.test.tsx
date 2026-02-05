import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import TaskLogPanel from './TaskLogPanel';

vi.mock('./TerminalSession', () => ({
  default: () => <div data-testid="terminal" />,
}));

describe('TaskLogPanel', () => {
  it('renders entries and triggers actions', () => {
    const onToggle = vi.fn();
    const onClear = vi.fn();
    const onEntryClick = vi.fn();

    render(
      <TaskLogPanel
        entries={[
          {
            id: '1',
            timestamp: new Date('2026-02-05T10:00:00Z'),
            level: 'error',
            message: 'Failed',
            jobId: 'job1',
          },
        ]}
        isVisible={true}
        onToggle={onToggle}
        onClear={onClear}
        onEntryClick={onEntryClick}
      />
    );

    fireEvent.click(screen.getByText('Clear'));
    expect(onClear).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('Failed'));
    expect(onEntryClick).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('v'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
