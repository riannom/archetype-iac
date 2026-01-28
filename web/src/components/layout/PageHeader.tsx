import React from 'react';
import { ArchetypeIcon } from '../icons';
import { Button } from '../ui/Button';

export interface PageHeaderProps {
  title?: string;
  subtitle?: string;
  onBack?: () => void;
  onThemeClick?: () => void;
  onModeToggle?: () => void;
  effectiveMode?: 'light' | 'dark';
  actions?: React.ReactNode;
  className?: string;
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  title = 'ARCHETYPE',
  subtitle,
  onBack,
  onThemeClick,
  onModeToggle,
  effectiveMode,
  actions,
  className = '',
}) => {
  return (
    <header
      className={`
        h-20 border-b border-stone-200 dark:border-stone-800
        bg-white/30 dark:bg-stone-900/30
        flex items-center justify-between px-10
        ${className}
      `.trim().replace(/\s+/g, ' ')}
    >
      {/* Left side - Logo and title */}
      <div className="flex items-center gap-4">
        <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
        <div>
          <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">
            {title}
          </h1>
          {subtitle && (
            <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">
              {subtitle}
            </p>
          )}
        </div>
      </div>

      {/* Right side - Actions */}
      <div className="flex items-center gap-3">
        {onBack && (
          <Button
            variant="secondary"
            size="sm"
            leftIcon="fa-solid fa-arrow-left"
            onClick={onBack}
          >
            <span className="text-[10px] font-bold uppercase">Back</span>
          </Button>
        )}

        {actions}

        {onThemeClick && (
          <Button
            variant="secondary"
            size="icon"
            onClick={onThemeClick}
            title="Theme Settings"
          >
            <i className="fa-solid fa-palette text-sm" />
          </Button>
        )}

        {onModeToggle && effectiveMode && (
          <Button
            variant="secondary"
            size="icon"
            onClick={onModeToggle}
            title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
          >
            <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`} />
          </Button>
        )}
      </div>
    </header>
  );
};

export default PageHeader;
