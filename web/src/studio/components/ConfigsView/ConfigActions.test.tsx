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
});
