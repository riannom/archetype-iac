import React from 'react';
import { render } from '@testing-library/react';
import { ArchetypeIcon } from './ArchetypeIcon';

describe('ArchetypeIcon', () => {
  it('renders a scalable icon', () => {
    const { container } = render(<ArchetypeIcon size={48} color="#111" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute('width', '48');
  });
});
