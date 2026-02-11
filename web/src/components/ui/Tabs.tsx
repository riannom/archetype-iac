import React, { createContext, useContext } from 'react';

export type TabsVariant = 'underline' | 'pills';

interface TabsContextValue {
  activeTab: string;
  setActiveTab: (id: string) => void;
  variant: TabsVariant;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabsContext() {
  const context = useContext(TabsContext);
  if (!context) {
    throw new Error('Tabs components must be used within a Tabs provider');
  }
  return context;
}

export interface TabsProps {
  activeTab: string;
  onTabChange: (id: string) => void;
  variant?: TabsVariant;
  children: React.ReactNode;
  className?: string;
}

export const Tabs: React.FC<TabsProps> = ({
  activeTab,
  onTabChange,
  variant = 'underline',
  children,
  className = '',
}) => {
  return (
    <TabsContext.Provider value={{ activeTab, setActiveTab: onTabChange, variant }}>
      <div className={className}>
        {children}
      </div>
    </TabsContext.Provider>
  );
};

export interface TabListProps {
  children: React.ReactNode;
  className?: string;
}

export const TabList: React.FC<TabListProps> = ({ children, className = '' }) => {
  const { variant } = useTabsContext();

  const baseStyles = variant === 'underline'
    ? 'flex border-b border-stone-200 dark:border-stone-700'
    : 'flex gap-1 p-1 rounded-lg glass-control';

  return (
    <div className={`${baseStyles} ${className}`}>
      {children}
    </div>
  );
};

export interface TabProps {
  id: string;
  icon?: string;
  children: React.ReactNode;
  disabled?: boolean;
  className?: string;
}

export const Tab: React.FC<TabProps> = ({
  id,
  icon,
  children,
  disabled = false,
  className = '',
}) => {
  const { activeTab, setActiveTab, variant } = useTabsContext();
  const isActive = activeTab === id;

  const underlineStyles = isActive
    ? 'text-sage-600 dark:text-sage-400 border-b-2 border-sage-600 dark:border-sage-400'
    : 'text-stone-500 dark:text-stone-400 hover:text-stone-700 dark:hover:text-stone-300 border-b-2 border-transparent';

  const pillStyles = isActive
    ? 'glass-surface text-stone-900 dark:text-stone-100 shadow-sm'
    : 'text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-stone-200';

  const baseStyles = variant === 'underline'
    ? `px-4 py-2 text-sm font-semibold transition-all ${underlineStyles}`
    : `px-3 py-1.5 text-sm font-medium rounded-md transition-all ${pillStyles}`;

  return (
    <button
      onClick={() => !disabled && setActiveTab(id)}
      disabled={disabled}
      className={`
        flex items-center gap-2
        ${baseStyles}
        ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
        ${className}
      `.trim().replace(/\s+/g, ' ')}
    >
      {icon && <i className={icon} />}
      {children}
    </button>
  );
};

export interface TabPanelProps {
  id: string;
  children: React.ReactNode;
  className?: string;
}

export const TabPanel: React.FC<TabPanelProps> = ({
  id,
  children,
  className = '',
}) => {
  const { activeTab } = useTabsContext();

  if (activeTab !== id) return null;

  return (
    <div className={className}>
      {children}
    </div>
  );
};

export default Tabs;
