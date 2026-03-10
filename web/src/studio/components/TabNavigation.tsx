import React from 'react';

type StudioView = 'designer' | 'configs' | 'logs' | 'runtime' | 'tests' | 'scenarios' | 'infra';

interface TabNavigationProps {
  view: StudioView;
  onViewChange: (view: StudioView) => void;
  agents: unknown[];
  isDesignerView: boolean;
}

const TABS: { key: StudioView; label: string }[] = [
  { key: 'designer', label: 'Designer' },
  { key: 'runtime', label: 'Runtime' },
  { key: 'configs', label: 'Configs' },
  { key: 'logs', label: 'Logs' },
  { key: 'tests', label: 'Tests' },
  { key: 'scenarios', label: 'Scenarios' },
];

const TabNavigation: React.FC<TabNavigationProps> = ({ view, onViewChange, agents }) => {
  const tabClass = (key: StudioView) =>
    `h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
      view === key
        ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
        : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
    }`;

  return (
    <div className="h-10 bg-white/35 dark:bg-black/35 backdrop-blur-md border-b border-stone-200/70 dark:border-black/70 flex px-6 items-center gap-1 shrink-0">
      {TABS.map(({ key, label }) => (
        <button key={key} onClick={() => onViewChange(key)} className={tabClass(key)}>
          {label}
        </button>
      ))}
      {agents.length > 1 && (
        <button onClick={() => onViewChange('infra')} className={tabClass('infra')}>
          Infra
        </button>
      )}
    </div>
  );
};

export default TabNavigation;
