import React, { useState, useRef, useEffect } from 'react';

// Custom dropdown component for agent selection with proper dark mode support
const AgentDropdown: React.FC<{
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  agents: { id: string; name: string }[];
}> = ({ value, onChange, disabled, agents }) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const selectedAgent = agents.find(a => a.id === value);
  const displayText = selectedAgent ? selectedAgent.name : 'Auto (any available agent)';

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as HTMLElement)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="space-y-2">
      <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">Agent Placement</label>
      <div ref={dropdownRef} className="relative">
        <button
          type="button"
          onClick={() => !disabled && setIsOpen(!isOpen)}
          disabled={disabled}
          className={`w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-left flex items-center justify-between ${
            disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:border-stone-400 dark:hover:border-stone-600'
          } ${isOpen ? 'border-sage-500 dark:border-sage-500' : ''}`}
        >
          <span className={value ? 'text-stone-900 dark:text-stone-100' : 'text-stone-500 dark:text-stone-400'}>
            {displayText}
          </span>
          <i className={`fa-solid fa-chevron-down text-[10px] text-stone-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
        </button>
        {isOpen && (
          <div className="absolute z-50 w-full mt-1 bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded-lg shadow-lg overflow-hidden">
            <button
              type="button"
              onClick={() => { onChange(''); setIsOpen(false); }}
              className={`w-full px-3 py-2 text-sm text-left transition-colors focus:outline-none ${
                !value
                  ? 'bg-sage-100 text-sage-700 hover:bg-sage-100 focus:bg-sage-100 active:bg-sage-100 dark:bg-sage-950 dark:text-sage-300 dark:hover:bg-sage-950 dark:focus:bg-sage-950 dark:active:bg-sage-950'
                  : 'bg-stone-50 text-stone-700 hover:bg-stone-100 focus:bg-stone-100 active:bg-stone-100 dark:bg-stone-900 dark:text-stone-300 dark:hover:bg-stone-800 dark:focus:bg-stone-800 dark:active:bg-stone-800'
              }`}
            >
              Auto (any available agent)
            </button>
            {agents.map((agent) => (
              <button
                key={agent.id}
                type="button"
                onClick={() => { onChange(agent.id); setIsOpen(false); }}
                className={`w-full px-3 py-2 text-sm text-left transition-colors focus:outline-none ${
                  value === agent.id
                    ? 'bg-sage-100 text-sage-700 hover:bg-sage-100 focus:bg-sage-100 active:bg-sage-100 dark:bg-sage-950 dark:text-sage-300 dark:hover:bg-sage-950 dark:focus:bg-sage-950 dark:active:bg-sage-950'
                    : 'bg-stone-50 text-stone-700 hover:bg-stone-100 focus:bg-stone-100 active:bg-stone-100 dark:bg-stone-900 dark:text-stone-300 dark:hover:bg-stone-800 dark:focus:bg-stone-800 dark:active:bg-stone-800'
                }`}
              >
                {agent.name}
              </button>
            ))}
          </div>
        )}
      </div>
      <p className="text-[9px] text-stone-400 dark:text-stone-500">
        {disabled ? 'Stop node to change agent placement' : 'Select which agent runs this node'}
      </p>
    </div>
  );
};

export default AgentDropdown;
