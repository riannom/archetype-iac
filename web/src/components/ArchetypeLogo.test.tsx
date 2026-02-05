import React from 'react';
import { render } from '@testing-library/react';
import { ArchetypeLogo } from './ArchetypeLogo';

describe('ArchetypeLogo', () => {
  it('renders the SVG mark', () => {
    const { container } = render(<ArchetypeLogo className="h-6 w-6" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute('viewBox', '0 0 64 64');
  });
});
