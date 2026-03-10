import React from 'react';

type LabView = 'designer' | 'configs' | 'logs' | 'runtime' | 'tests' | 'scenarios' | 'infra';

interface ViewTabBarProps {
  view: LabView;
  onViewChange: (view: LabView) => void;
  showInfraTab: boolean;
}

const TAB_DEFINITIONS: Array<{ id: LabView; label: string }> = [
  { id: 'designer', label: 'Designer' },
  { id: 'runtime', label: 'Runtime' },
  { id: 'configs', label: 'Configs' },
  { id: 'logs', label: 'Logs' },
  { id: 'tests', label: 'Tests' },
  { id: 'scenarios', label: 'Scenarios' },
];

function ViewTabBar({ view, onViewChange, showInfraTab }: ViewTabBarProps): React.ReactElement {
  const tabs = showInfraTab
    ? [...TAB_DEFINITIONS, { id: 'infra' as LabView, label: 'Infra' }]
    : TAB_DEFINITIONS;

  return (
    <div className="h-10 bg-white/35 dark:bg-black/35 backdrop-blur-md border-b border-stone-200/70 dark:border-black/70 flex px-6 items-center gap-1 shrink-0">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onViewChange(tab.id)}
          className={`h-full px-4 text-[10px] font-black uppercase border-b-2 transition-all ${
            view === tab.id
              ? 'text-sage-700 dark:text-sage-500 border-sage-700 dark:border-sage-500'
              : 'text-stone-700 dark:text-stone-300 border-transparent hover:text-stone-900 dark:hover:text-stone-100'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export type { LabView };
export default ViewTabBar;
