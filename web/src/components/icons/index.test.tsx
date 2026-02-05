import React from 'react';
import { render } from '@testing-library/react';
import { ArchetypeIcon, ArchetypeIconDefault } from './index';

describe('icons index', () => {
  it('exports icon components', () => {
    const { container } = render(
      <div>
        <ArchetypeIcon size={32} />
        <ArchetypeIconDefault />
      </div>
    );
    expect(container.querySelectorAll('svg').length).toBe(2);
  });
});
