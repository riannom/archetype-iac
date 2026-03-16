import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Skeleton, SkeletonCard } from './Skeleton';

describe('Skeleton', () => {
  it('renders a single skeleton element by default', () => {
    const { container } = render(<Skeleton />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveClass('skeleton-shimmer');
  });

  it('renders multiple items with count prop', () => {
    const { container } = render(<Skeleton count={3} />);
    const items = container.querySelectorAll('.skeleton-shimmer');
    expect(items).toHaveLength(3);
  });

  it('applies custom className', () => {
    const { container } = render(<Skeleton className="custom" />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveClass('custom');
  });

  it('applies width and height styles', () => {
    const { container } = render(<Skeleton width={100} height={20} />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveStyle({ width: '100px', height: '20px' });
  });

  it('accepts string dimensions', () => {
    const { container } = render(<Skeleton width="50%" height="2rem" />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveStyle({ width: '50%', height: '2rem' });
  });

  it('renders circular variant with rounded-full', () => {
    const { container } = render(<Skeleton variant="circular" />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveClass('rounded-full');
  });

  it('renders rectangular variant with rounded-lg', () => {
    const { container } = render(<Skeleton variant="rectangular" />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveClass('rounded-lg');
  });
});

describe('SkeletonCard', () => {
  it('renders multiple skeleton elements', () => {
    const { container } = render(<SkeletonCard />);
    const items = container.querySelectorAll('.skeleton-shimmer');
    expect(items.length).toBeGreaterThan(0);
  });

  it('has glass-surface styling', () => {
    const { container } = render(<SkeletonCard />);
    const card = container.firstChild as HTMLElement;
    expect(card).toHaveClass('glass-surface');
  });
});
