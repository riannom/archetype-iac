import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import TaskLogPanel from './TaskLogPanel';

vi.mock('./TerminalSession', () => ({
  default: () => <div data-testid="terminal" />,
}));

vi.mock('../../theme', () => ({
  useTheme: () => ({ effectiveMode: 'light' }),
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

  it('toggles auto-refresh when enabled', () => {
    const onToggleAutoUpdate = vi.fn();

    render(
      <TaskLogPanel
        entries={[]}
        isVisible={true}
        onToggle={vi.fn()}
        onClear={vi.fn()}
        autoUpdateEnabled={true}
        onToggleAutoUpdate={onToggleAutoUpdate}
      />
    );

    fireEvent.click(screen.getByText('Auto-refresh'));
    expect(onToggleAutoUpdate).toHaveBeenCalledWith(false);
  });

  it('auto-scrolls to latest entry when auto-refresh is enabled', async () => {
    const baseProps = {
      isVisible: true,
      onToggle: vi.fn(),
      onClear: vi.fn(),
      autoUpdateEnabled: true,
    };
    const entries = [
      { id: '1', timestamp: new Date('2026-02-05T10:00:00Z'), level: 'info' as const, message: 'one' },
    ];
    const { container, rerender } = render(<TaskLogPanel {...baseProps} entries={entries} />);
    const logContainer = container.querySelector('.h-full.overflow-y-auto.font-mono.text-\\[11px\\]') as HTMLDivElement;
    expect(logContainer).toBeTruthy();

    Object.defineProperty(logContainer, 'scrollHeight', { value: 420, configurable: true });
    logContainer.scrollTop = 0;

    rerender(
      <TaskLogPanel
        {...baseProps}
        entries={[...entries, { id: '2', timestamp: new Date('2026-02-05T10:00:01Z'), level: 'info', message: 'two' }]}
      />
    );

    await waitFor(() => {
      expect(logContainer.scrollTop).toBe(420);
    });
  });

  it('does not auto-scroll when auto-refresh is disabled', async () => {
    const baseProps = {
      isVisible: true,
      onToggle: vi.fn(),
      onClear: vi.fn(),
      autoUpdateEnabled: false,
    };
    const entries = [
      { id: '1', timestamp: new Date('2026-02-05T10:00:00Z'), level: 'info' as const, message: 'one' },
    ];
    const { container, rerender } = render(<TaskLogPanel {...baseProps} entries={entries} />);
    const logContainer = container.querySelector('.h-full.overflow-y-auto.font-mono.text-\\[11px\\]') as HTMLDivElement;
    expect(logContainer).toBeTruthy();

    Object.defineProperty(logContainer, 'scrollHeight', { value: 420, configurable: true });
    logContainer.scrollTop = 0;

    rerender(
      <TaskLogPanel
        {...baseProps}
        entries={[...entries, { id: '2', timestamp: new Date('2026-02-05T10:00:01Z'), level: 'info', message: 'two' }]}
      />
    );

    await waitFor(() => {
      expect(logContainer.scrollTop).toBe(0);
    });
  });
});
