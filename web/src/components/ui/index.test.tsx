import React from 'react';
import { render } from '@testing-library/react';
import { Button, Badge, Tabs, TabList, Tab, TabPanel } from './index';

describe('ui index exports', () => {
  it('exports primitives', () => {
    render(
      <div>
        <Button>Click</Button>
        <Badge>New</Badge>
        <Tabs activeTab="a" onTabChange={() => undefined}>
          <TabList>
            <Tab id="a">A</Tab>
          </TabList>
          <TabPanel id="a">Panel</TabPanel>
        </Tabs>
      </div>
    );
  });
});
