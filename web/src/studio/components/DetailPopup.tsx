import React from 'react';

interface DetailPopupProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  width?: string;
}

const DetailPopup: React.FC<DetailPopupProps> = ({
  isOpen,
  onClose,
  title,
  children,
  width = "max-w-lg"
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className={`relative bg-stone-50 dark:bg-stone-900 rounded-xl shadow-2xl w-full ${width} mx-4 max-h-[85vh] overflow-hidden`}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-stone-200 dark:border-stone-700">
          <h2 className="text-lg font-semibold text-stone-900 dark:text-stone-100">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 transition-colors"
          >
            <i className="fa-solid fa-xmark text-lg" />
          </button>
        </div>
        <div className="p-6 overflow-y-auto max-h-[calc(85vh-4rem)]">
          {children}
        </div>
      </div>
    </div>
  );
};

export default DetailPopup;
