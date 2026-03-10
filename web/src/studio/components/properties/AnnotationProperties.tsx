import React from 'react';
import { Annotation } from '../../types';

interface AnnotationPropertiesProps {
  annotation: Annotation;
  annotations: Annotation[];
  onUpdateAnnotation: (id: string, updates: Partial<Annotation>) => void;
  onDelete: (id: string) => void;
}

const AnnotationProperties: React.FC<AnnotationPropertiesProps> = ({
  annotation: ann,
  annotations,
  onUpdateAnnotation,
  onDelete,
}) => {
  // Z-order helper functions
  const getZIndexes = () => annotations.map(a => a.zIndex ?? 0);
  const getMaxZIndex = () => Math.max(...getZIndexes(), 0);
  const getMinZIndex = () => Math.min(...getZIndexes(), 0);

  const handleBringToFront = () => {
    onUpdateAnnotation(ann.id, { zIndex: getMaxZIndex() + 1 });
  };
  const handleBringForward = () => {
    onUpdateAnnotation(ann.id, { zIndex: (ann.zIndex ?? 0) + 1 });
  };
  const handleSendBackward = () => {
    onUpdateAnnotation(ann.id, { zIndex: (ann.zIndex ?? 0) - 1 });
  };
  const handleSendToBack = () => {
    onUpdateAnnotation(ann.id, { zIndex: getMinZIndex() - 1 });
  };

  return (
    <div className="w-80 bg-white dark:bg-stone-900 border-l border-stone-200 dark:border-stone-700 overflow-y-auto">
      <div className="p-4 border-b border-stone-200 dark:border-stone-700 flex justify-between items-center bg-stone-100/50 dark:bg-stone-800/50">
        <h2 className="text-sm font-bold uppercase tracking-wider text-sage-600 dark:text-sage-400">Annotation Settings</h2>
        <button onClick={() => onDelete(ann.id)} className="p-1.5 text-red-500 hover:bg-red-100 dark:hover:bg-red-950/30 rounded transition-all">
          <i className="fa-solid fa-trash-can"></i>
        </button>
      </div>
      <div className="p-6 space-y-6">
        {ann.type === 'text' && (
          <div className="space-y-2">
            <label className="text-[11px] font-bold text-stone-500 uppercase">Text Content</label>
            <textarea
              value={ann.text || ''}
              onChange={(e) => onUpdateAnnotation(ann.id, { text: e.target.value })}
              className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500 min-h-[80px]"
            />
          </div>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <label className="text-[11px] font-bold text-stone-500 uppercase tracking-tighter">Color</label>
            <input type="color" value={ann.color || '#65A30D'} onChange={(e) => onUpdateAnnotation(ann.id, { color: e.target.value })} className="w-full h-10 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded p-1 cursor-pointer" />
          </div>
          {(ann.type === 'text') && (
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-stone-500 uppercase tracking-tighter">Size</label>
              <input type="number" value={ann.fontSize || 14} onChange={(e) => onUpdateAnnotation(ann.id, { fontSize: parseInt(e.target.value) })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500" />
            </div>
          )}
        </div>

        {/* Dimensions for rect and circle */}
        {(ann.type === 'rect' || ann.type === 'circle') && (
          <div className="space-y-2">
            <label className="text-[11px] font-bold text-stone-500 uppercase tracking-tighter">Dimensions</label>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label className="text-[9px] font-bold text-stone-400 uppercase">{ann.type === 'circle' ? 'Diameter' : 'Width'}</label>
                <input
                  type="number"
                  value={ann.width || (ann.type === 'rect' ? 100 : 80)}
                  onChange={(e) => onUpdateAnnotation(ann.id, { width: Math.max(20, parseInt(e.target.value) || 20) })}
                  className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500"
                  min="20"
                />
              </div>
              {ann.type === 'rect' && (
                <div className="space-y-1">
                  <label className="text-[9px] font-bold text-stone-400 uppercase">Height</label>
                  <input
                    type="number"
                    value={ann.height || 60}
                    onChange={(e) => onUpdateAnnotation(ann.id, { height: Math.max(20, parseInt(e.target.value) || 20) })}
                    className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500"
                    min="20"
                  />
                </div>
              )}
            </div>
          </div>
        )}

        {/* Arrow endpoints */}
        {ann.type === 'arrow' && (
          <div className="space-y-2">
            <label className="text-[11px] font-bold text-stone-500 uppercase tracking-tighter">Endpoints</label>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label className="text-[9px] font-bold text-stone-400 uppercase">Start X</label>
                <input type="number" value={Math.round(ann.x)} onChange={(e) => onUpdateAnnotation(ann.id, { x: parseFloat(e.target.value) || 0 })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500" />
              </div>
              <div className="space-y-1">
                <label className="text-[9px] font-bold text-stone-400 uppercase">Start Y</label>
                <input type="number" value={Math.round(ann.y)} onChange={(e) => onUpdateAnnotation(ann.id, { y: parseFloat(e.target.value) || 0 })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500" />
              </div>
              <div className="space-y-1">
                <label className="text-[9px] font-bold text-stone-400 uppercase">End X</label>
                <input type="number" value={Math.round(ann.targetX ?? ann.x + 100)} onChange={(e) => onUpdateAnnotation(ann.id, { targetX: parseFloat(e.target.value) || 0 })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500" />
              </div>
              <div className="space-y-1">
                <label className="text-[9px] font-bold text-stone-400 uppercase">End Y</label>
                <input type="number" value={Math.round(ann.targetY ?? ann.y + 100)} onChange={(e) => onUpdateAnnotation(ann.id, { targetY: parseFloat(e.target.value) || 0 })} className="w-full bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 rounded px-3 py-2 text-sm text-stone-900 dark:text-stone-100 focus:outline-none focus:border-sage-500" />
              </div>
            </div>
          </div>
        )}

        {/* Layer (Z-Order) Controls */}
        <div className="space-y-2">
          <label className="text-[11px] font-bold text-stone-500 uppercase tracking-tighter">Layer</label>
          <div className="grid grid-cols-4 gap-1">
            <button
              onClick={handleBringToFront}
              className="flex items-center justify-center gap-1 py-2 glass-control text-stone-700 dark:text-stone-300 text-[9px] font-bold rounded transition-colors border border-stone-200 dark:border-stone-700"
              title="Bring to Front"
            >
              <i className="fa-solid fa-angles-up"></i>
            </button>
            <button
              onClick={handleBringForward}
              className="flex items-center justify-center gap-1 py-2 glass-control text-stone-700 dark:text-stone-300 text-[9px] font-bold rounded transition-colors border border-stone-200 dark:border-stone-700"
              title="Bring Forward"
            >
              <i className="fa-solid fa-angle-up"></i>
            </button>
            <button
              onClick={handleSendBackward}
              className="flex items-center justify-center gap-1 py-2 glass-control text-stone-700 dark:text-stone-300 text-[9px] font-bold rounded transition-colors border border-stone-200 dark:border-stone-700"
              title="Send Backward"
            >
              <i className="fa-solid fa-angle-down"></i>
            </button>
            <button
              onClick={handleSendToBack}
              className="flex items-center justify-center gap-1 py-2 glass-control text-stone-700 dark:text-stone-300 text-[9px] font-bold rounded transition-colors border border-stone-200 dark:border-stone-700"
              title="Send to Back"
            >
              <i className="fa-solid fa-angles-down"></i>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AnnotationProperties;
