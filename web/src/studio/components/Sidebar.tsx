
import React from 'react';
import { DeviceModel, AnnotationType } from '../types';

interface SidebarProps {
  categories: { name: string; models: DeviceModel[]; subCategories?: { name: string; models: DeviceModel[] }[] }[];
  onAddDevice: (model: DeviceModel) => void;
  onAddAnnotation: (type: AnnotationType) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ categories, onAddDevice, onAddAnnotation }) => {
  const renderModel = (model: DeviceModel) => (
    <div
      key={model.id}
      draggable
      onDragEnd={() => onAddDevice(model)}
      onClick={() => onAddDevice(model)}
      className="group flex items-center p-2 bg-transparent hover:bg-slate-100 dark:hover:bg-slate-800 border border-transparent hover:border-slate-200 dark:hover:border-slate-700 rounded-lg cursor-grab active:cursor-grabbing transition-all"
    >
      <div className="w-8 h-8 rounded bg-white dark:bg-slate-800 flex items-center justify-center mr-3 group-hover:bg-blue-100 dark:group-hover:bg-blue-900/30 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors border border-slate-200 dark:border-slate-700 shadow-sm">
        <i className={`fa-solid ${model.icon} text-xs`}></i>
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] font-semibold text-slate-700 dark:text-slate-200 truncate group-hover:text-slate-900 dark:group-hover:text-white">{model.name}</div>
        <div className="text-[9px] text-slate-400 dark:text-slate-500 font-medium truncate italic">{model.versions[0]}</div>
      </div>
      <div className="opacity-0 group-hover:opacity-100 transition-opacity">
        <i className="fa-solid fa-plus-circle text-xs text-blue-500"></i>
      </div>
    </div>
  );

  const annotationTools: { type: AnnotationType, icon: string, label: string }[] = [
    { type: 'text', icon: 'fa-font', label: 'Label' },
    { type: 'rect', icon: 'fa-square', label: 'Box' },
    { type: 'circle', icon: 'fa-circle', label: 'Zone' },
    { type: 'arrow', icon: 'fa-arrow-right', label: 'Flow' },
    { type: 'caption', icon: 'fa-comment', label: 'Note' },
  ];

  return (
    <div className="w-64 bg-white/40 dark:bg-slate-900/40 backdrop-blur-md border-r border-slate-200 dark:border-slate-800 flex flex-col h-full overflow-hidden">
      <div className="p-4 border-b border-slate-200 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/30">
        <h2 className="text-sm font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400 flex items-center gap-2">
          <i className="fa-solid fa-boxes-stacked text-blue-600 dark:text-blue-500"></i>
          Library
        </h2>
      </div>
      
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="mb-4">
          <div className="px-4 py-2 flex items-center justify-between text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest bg-slate-100/50 dark:bg-slate-800/20 border-y border-slate-200 dark:border-slate-800 sticky top-0 z-10">
            <span>Tools</span>
          </div>
          <div className="p-2 grid grid-cols-2 gap-2">
            {annotationTools.map(tool => (
              <button
                key={tool.type}
                onClick={() => onAddAnnotation(tool.type)}
                className="flex flex-col items-center justify-center p-2 rounded-lg bg-white dark:bg-slate-800/50 hover:bg-slate-50 dark:hover:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:border-blue-300 dark:hover:border-blue-500/50 transition-all gap-1 group shadow-sm"
              >
                <i className={`fa-solid ${tool.icon} text-slate-400 group-hover:text-blue-600 dark:group-hover:text-blue-400 text-xs`}></i>
                <span className="text-[9px] text-slate-500 dark:text-slate-500 group-hover:text-slate-700 dark:group-hover:text-slate-200 font-bold">{tool.label}</span>
              </button>
            ))}
          </div>
        </div>

        {categories.map((category) => (
          <div key={category.name} className="mb-2">
            <div className="px-4 py-2 flex items-center justify-between text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest bg-slate-100/50 dark:bg-slate-800/20 border-y border-slate-200 dark:border-slate-800 sticky top-0 z-10">
              <span>{category.name}</span>
            </div>
            
            <div className="p-1 space-y-1">
              {category.subCategories ? (
                category.subCategories.map(sub => (
                  <div key={sub.name} className="mt-2">
                    <div className="px-3 py-1 text-[9px] font-bold text-slate-400 dark:text-slate-600 uppercase flex items-center gap-2">
                      <div className="h-px flex-1 bg-slate-200 dark:bg-slate-800"></div>
                      {sub.name}
                      <div className="h-px flex-1 bg-slate-200 dark:bg-slate-800"></div>
                    </div>
                    <div className="space-y-1 mt-1 px-1">
                      {sub.models.map(renderModel)}
                    </div>
                  </div>
                ))
              ) : (
                <div className="px-1 pt-1">
                  {category.models?.map(renderModel)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      
      <div className="p-4 border-t border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-950/50">
        <div className="flex flex-col gap-2 p-3 bg-blue-500/5 border border-blue-500/10 rounded-lg">
          <div className="flex items-center gap-2 text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase tracking-tight">
            <i className="fa-solid fa-lightbulb"></i>
            <span>IaC Canvas</span>
          </div>
          <p className="text-[9px] text-slate-500 dark:text-slate-400 leading-relaxed">
            Drag devices onto the grid to build your topology.
          </p>
        </div>
      </div>
    </div>
  );
};

export default Sidebar;
