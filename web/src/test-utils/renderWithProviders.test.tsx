import React from 'react';
import { renderWithProviders, screen } from './renderWithProviders';

describe('renderWithProviders', () => {
  it('renders with theme and router wrappers', () => {
    renderWithProviders(<div>Wrapped</div>, { useMemoryRouter: true });
    expect(screen.getByText('Wrapped')).toBeInTheDocument();
  });
});
