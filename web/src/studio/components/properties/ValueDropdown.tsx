import React, { useState, useRef, useEffect } from 'react';

const ValueDropdown: React.FC<{
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  disabled?: boolean;
}> = ({ label, value, options, onChange, disabled = false }) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

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
      <label className="text-[10px] font-bold text-stone-500 uppercase tracking-widest">{label}</label>
      <div ref={dropdownRef} className="relative">
        <button
          type="button"
          onClick={() => !disabled && setIsOpen(!isOpen)}
          disabled={disabled}
          className={`w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg px-3 py-2 text-sm text-left flex items-center justify-between ${
            disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:border-stone-400 dark:hover:border-stone-600'
          } ${isOpen ? 'border-sage-500 dark:border-sage-500' : ''}`}
        >
          <span className="text-stone-900 dark:text-stone-100">{value}</span>
          <i className={`fa-solid fa-chevron-down text-[10px] text-stone-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
        </button>
        {isOpen && (
          <div className="absolute z-50 w-full mt-1 bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-stone-700 rounded-lg shadow-lg overflow-hidden">
            {options.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => { onChange(option); setIsOpen(false); }}
                className={`w-full px-3 py-2 text-sm text-left transition-colors focus:outline-none ${
                  value === option
                    ? 'bg-sage-100 text-sage-700 hover:bg-sage-100 focus:bg-sage-100 active:bg-sage-100 dark:bg-sage-950 dark:text-sage-300 dark:hover:bg-sage-950 dark:focus:bg-sage-950 dark:active:bg-sage-950'
                    : 'bg-stone-50 text-stone-700 hover:bg-stone-100 focus:bg-stone-100 active:bg-stone-100 dark:bg-stone-900 dark:text-stone-300 dark:hover:bg-stone-800 dark:focus:bg-stone-800 dark:active:bg-stone-800'
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default ValueDropdown;
