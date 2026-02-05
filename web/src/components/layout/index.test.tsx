import React from 'react';
import { render } from '@testing-library/react';
import { PageHeader, PageLayout } from './index';

describe('layout index exports', () => {
  it('exports layout components', () => {
    render(
      <PageLayout>
        <PageHeader title="Layout" />
      </PageLayout>
    );
  });
});
