import React from 'react';
import { Tooltip } from '../../../components/ui/Tooltip';

interface CanvasControlsProps {
  setZoom: React.Dispatch<React.SetStateAction<number>>;
  centerCanvas: () => void;
  fitToScreen: () => void;
  agents: { id: string; name: string }[];
  showAgentIndicators: boolean;
  onToggleAgentIndicators?: () => void;
}

export const CanvasControls: React.FC<CanvasControlsProps> = ({
  setZoom,
  centerCanvas,
  fitToScreen,
  agents,
  showAgentIndicators,
  onToggleAgentIndicators,
}) => {
  return (
    <div className="absolute bottom-6 left-6 flex flex-col gap-2 z-30">
      <div className="bg-white/80 dark:bg-stone-900/80 backdrop-blur-md border border-stone-200 dark:border-stone-700 rounded-lg flex flex-col overflow-hidden shadow-lg">
        <button onClick={() => setZoom(prev => Math.min(prev * 1.2, 5))} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors border-b border-stone-200 dark:border-stone-700"><i className="fa-solid fa-plus"></i></button>
        <button onClick={() => setZoom(prev => Math.max(prev / 1.2, 0.1))} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"><i className="fa-solid fa-minus"></i></button>
      </div>
      <div className="bg-white/80 dark:bg-stone-900/80 backdrop-blur-md border border-stone-200 dark:border-stone-700 rounded-lg flex flex-col overflow-hidden shadow-lg">
        <Tooltip content="Center (zoom out if needed)" placement="right">
          <button title="Center (zoom out if needed)" onClick={centerCanvas} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors border-b border-stone-200 dark:border-stone-700"><i className="fa-solid fa-crosshairs"></i></button>
        </Tooltip>
        <Tooltip content="Fit to screen" placement="right">
          <button title="Fit to screen" onClick={fitToScreen} className="p-3 text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"><i className="fa-solid fa-maximize"></i></button>
        </Tooltip>
      </div>
      {/* Agent indicator toggle - only show when multiple agents */}
      {agents.length > 1 && onToggleAgentIndicators && (
        <div className="bg-white/80 dark:bg-stone-900/80 backdrop-blur-md border border-stone-200 dark:border-stone-700 rounded-lg flex flex-col overflow-hidden shadow-lg">
          <Tooltip content={showAgentIndicators ? 'Hide agent indicators' : 'Show agent indicators'} placement="right">
            <button
              onClick={onToggleAgentIndicators}
              className={`p-3 transition-colors ${showAgentIndicators ? 'text-sage-600 dark:text-sage-400 bg-sage-500/10' : 'text-stone-500 dark:text-stone-400 hover:text-sage-600 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-stone-800'}`}
              title={showAgentIndicators ? 'Hide agent indicators' : 'Show agent indicators'}
            >
              <i className="fa-solid fa-server"></i>
            </button>
          </Tooltip>
        </div>
      )}
    </div>
  );
};
