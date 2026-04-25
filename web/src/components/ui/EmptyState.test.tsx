import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EmptyState } from './EmptyState';

describe('EmptyState', () => {
  it('renders title', () => {
    render(<EmptyState title="No items" />);
    expect(screen.getByText('No items')).toBeInTheDocument();
  });

  it('renders description when provided', () => {
    render(<EmptyState title="Empty" description="Nothing to show" />);
    expect(screen.getByText('Nothing to show')).toBeInTheDocument();
  });

  it('does not render description when not provided', () => {
    const { container } = render(<EmptyState title="Empty" />);
    const paragraphs = container.querySelectorAll('p');
    expect(paragraphs).toHaveLength(0);
  });

  it('renders action button when provided', () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="Empty"
        action={{ label: 'Create', onClick }}
      />,
    );
    expect(screen.getByRole('button', { name: 'Create' })).toBeInTheDocument();
  });

  it('calls action onClick when button clicked', async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <EmptyState
        title="Empty"
        action={{ label: 'Add', onClick }}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Add' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not render button when no action provided', () => {
    render(<EmptyState title="Empty" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(
      <EmptyState title="Test" className="custom-class" />,
    );
    expect(container.firstChild).toHaveClass('custom-class');
  });

  it('renders icon', () => {
    const { container } = render(
      <EmptyState title="Test" icon="fa-solid fa-folder" />,
    );
    expect(container.querySelector('i.fa-folder')).toBeInTheDocument();
  });

  it('uses compact spacing when compact=true', () => {
    const { container } = render(<EmptyState title="Test" compact />);
    expect(container.firstChild).toHaveClass('py-8');
    expect(container.firstChild).not.toHaveClass('py-16');
  });

  it('renders the action icon when action.icon is provided', () => {
    const { container } = render(
      <EmptyState
        title="Test"
        action={{ label: 'Create', onClick: () => {}, icon: 'fa-solid fa-plus' }}
      />,
    );
    expect(container.querySelector('button i.fa-plus')).toBeInTheDocument();
  });
});
