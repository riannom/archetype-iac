
import React, { useState, useEffect } from 'react';

const StatusBar: React.FC = () => {
  const [stats, setStats] = useState({
    cpu: 12,
    mem: 45,
    disk: 28,
    uptime: '02:45:12'
  });

  useEffect(() => {
    const interval = setInterval(() => {
      setStats(prev => ({
        ...prev,
        cpu: Math.max(5, Math.min(95, prev.cpu + (Math.random() * 6 - 3))),
        mem: Math.max(20, Math.min(90, prev.mem + (Math.random() * 2 - 1))),
      }));
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  const getResourceColor = (val: number) => {
    if (val > 85) return 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]';
    if (val > 65) return 'bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.5)]';
    return 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]';
  };

  return (
    <div className="h-8 bg-white/90 dark:bg-stone-900/90 backdrop-blur-md border-t border-stone-200 dark:border-stone-700 flex items-center justify-between px-4 z-50 shrink-0 text-[10px] font-bold tracking-tight">
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></div>
          <span className="text-stone-500 dark:text-stone-400 uppercase tracking-widest">System Health:</span>
          <span className="text-stone-900 dark:text-white">OPTIMAL</span>
        </div>

        <div className="h-3 w-px bg-stone-200 dark:bg-stone-800"></div>

        <div className="flex items-center gap-3 w-32">
          <span className="text-stone-400">CPU</span>
          <div className="flex-1 h-1.5 bg-stone-100 dark:bg-stone-800 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-1000 ${getResourceColor(stats.cpu)}`}
              style={{ width: `${stats.cpu}%` }}
            ></div>
          </div>
          <span className="text-stone-600 dark:text-stone-300 w-6 text-right">{Math.round(stats.cpu)}%</span>
        </div>

        <div className="flex items-center gap-3 w-32">
          <span className="text-stone-400">MEM</span>
          <div className="flex-1 h-1.5 bg-stone-100 dark:bg-stone-800 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-1000 ${getResourceColor(stats.mem)}`}
              style={{ width: `${stats.mem}%` }}
            ></div>
          </div>
          <span className="text-stone-600 dark:text-stone-300 w-6 text-right">{Math.round(stats.mem)}%</span>
        </div>
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2 text-stone-500 dark:text-stone-500 hover:text-sage-600 dark:hover:text-sage-400 cursor-pointer transition-colors">
          <i className="fa-solid fa-clock-rotate-left"></i>
          <span className="uppercase">UPTIME: {stats.uptime}</span>
        </div>

        <div className="h-3 w-px bg-stone-200 dark:bg-stone-800"></div>

        <div className="flex items-center gap-1.5 bg-stone-100 dark:bg-stone-800 px-2 py-0.5 rounded border border-stone-200 dark:border-stone-700 text-sage-600 dark:text-sage-400 uppercase">
          <i className="fa-solid fa-code-branch text-[8px]"></i>
          <span>v2.4.0-STABLE</span>
        </div>
      </div>
    </div>
  );
};

export default StatusBar;
