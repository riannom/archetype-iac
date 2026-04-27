import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfigActions } from './ConfigActions';

describe('ConfigActions', () => {
  it('renders buttons and handles delete confirmation', () => {
    const onExtract = vi.fn();
    const onDownloadAll = vi.fn();
    const onDeleteAllOrphaned = vi.fn();

    render(
      <ConfigActions
        extracting={false}
        orphanedCount={2}
        onExtract={onExtract}
        onDownloadAll={onDownloadAll}
        onDeleteAllOrphaned={onDeleteAllOrphaned}
      />
    );

    fireEvent.click(screen.getByText('Extract Configs'));
    expect(onExtract).toHaveBeenCalled();

    fireEvent.click(screen.getByText('Download All'));
    expect(onDownloadAll).toHaveBeenCalled();

    fireEvent.click(screen.getByText('Delete Orphaned'));
    expect(screen.getByText('Delete Orphaned Configs')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Delete All'));
    expect(onDeleteAllOrphaned).toHaveBeenCalled();
  });

  it('disables extract while extracting', () => {
    render(
      <ConfigActions
        extracting={true}
        orphanedCount={0}
        onExtract={() => {}}
        onDownloadAll={() => {}}
        onDeleteAllOrphaned={() => {}}
      />
    );

    expect(screen.getByText('Extracting...')).toBeInTheDocument();
  });

  it('cancels delete confirmation without invoking onDeleteAllOrphaned', () => {
    const onDeleteAllOrphaned = vi.fn();
    render(
      <ConfigActions
        extracting={false}
        orphanedCount={3}
        onExtract={() => {}}
        onDownloadAll={() => {}}
        onDeleteAllOrphaned={onDeleteAllOrphaned}
      />,
    );

    fireEvent.click(screen.getByText('Delete Orphaned'));
    expect(screen.getByText('Delete Orphaned Configs')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Cancel'));
    expect(onDeleteAllOrphaned).not.toHaveBeenCalled();
    expect(screen.queryByText('Delete Orphaned Configs')).not.toBeInTheDocument();
  });

  it('hides the Delete Orphaned button when orphanedCount is 0', () => {
    render(
      <ConfigActions
        extracting={false}
        orphanedCount={0}
        onExtract={() => {}}
        onDownloadAll={() => {}}
        onDeleteAllOrphaned={() => {}}
      />,
    );

    expect(screen.queryByText('Delete Orphaned')).not.toBeInTheDocument();
  });

  it('uses singular "snapshot" copy when orphanedCount is exactly 1', () => {
    render(
      <ConfigActions
        extracting={false}
        orphanedCount={1}
        onExtract={() => {}}
        onDownloadAll={() => {}}
        onDeleteAllOrphaned={() => {}}
      />,
    );

    fireEvent.click(screen.getByText('Delete Orphaned'));
    // Singular form (no trailing 's')
    expect(
      screen.getByText(/Delete all 1 orphaned config snapshot\?/),
    ).toBeInTheDocument();
  });
});
