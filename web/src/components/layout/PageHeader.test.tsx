import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import { PageHeader } from './PageHeader';

vi.mock('../VersionBadge', () => ({
  VersionBadge: () => <div data-testid="version-badge" />,
}));

describe('PageHeader', () => {
  it('renders title, subtitle, and actions', () => {
    const onBack = vi.fn();
    const onThemeClick = vi.fn();
    const onModeToggle = vi.fn();

    render(
      <PageHeader
        title="Archetype"
        subtitle="Labs"
        onBack={onBack}
        onThemeClick={onThemeClick}
        onModeToggle={onModeToggle}
        effectiveMode="dark"
        actions={<button>Action</button>}
      />
    );

    expect(screen.getByText('Archetype')).toBeInTheDocument();
    expect(screen.getByText('Labs')).toBeInTheDocument();
    expect(screen.getByTestId('version-badge')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Back'));
    fireEvent.click(screen.getByTitle('Theme Settings'));
    fireEvent.click(screen.getByTitle('Switch to light mode'));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(onThemeClick).toHaveBeenCalledTimes(1);
    expect(onModeToggle).toHaveBeenCalledTimes(1);
  });
});
