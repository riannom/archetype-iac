
import React from 'react';
import { Topology } from '../types';

interface DashboardProps {
  topologies: Topology[];
  onSelect: (topology: Topology) => void;
  onCreate: () => void;
  onDelete: (name: string) => void;
  onLogout: () => void;
  username: string;
}

const Dashboard: React.FC<DashboardProps> = ({ topologies, onSelect, onCreate, onDelete, onLogout, username }) => {
  return (
    <div className="min-h-screen bg-slate-950 flex flex-col overflow-hidden">
      <header className="h-20 border-b border-slate-800 bg-slate-900/30 flex items-center justify-between px-10">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-blue-600 rounded-xl flex items-center justify-center shadow-lg shadow-blue-900/20 border border-blue-400/30">
            <i className="fa-solid fa-bolt-lightning text-white"></i>
          </div>
          <div>
            <h1 className="text-xl font-black text-white tracking-tight">AURA</h1>
            <p className="text-[10px] text-slate-500 font-bold uppercase tracking-widest text-blue-500">Visual Studio</p>
          </div>
        </div>

        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3 pr-6 border-r border-slate-800">
            <div className="text-right">
              <div className="text-xs font-bold text-white">{username}</div>
              <div className="text-[10px] text-green-500 font-bold uppercase">Administrator</div>
            </div>
            <div className="w-10 h-10 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center overflow-hidden">
               <img src={`https://api.dicebear.com/7.x/identicon/svg?seed=${username}`} alt="Avatar" />
            </div>
          </div>
          <button 
            onClick={onLogout}
            className="flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-red-900/20 text-slate-500 hover:text-red-500 border border-slate-700 rounded-lg transition-all"
            title="Log out"
          >
            <i className="fa-solid fa-right-from-bracket"></i>
            <span className="text-[10px] font-bold uppercase">Sign Out</span>
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-10 custom-scrollbar">
        <div className="max-w-6xl mx-auto">
          <div className="flex justify-between items-center mb-8">
            <div>
              <h2 className="text-2xl font-bold text-white">Your Workspace</h2>
              <p className="text-slate-500 text-sm mt-1">Manage, design and deploy your virtual network environments.</p>
            </div>
            <button 
              onClick={onCreate}
              className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2.5 rounded-xl font-bold text-sm shadow-lg shadow-blue-900/20 transition-all flex items-center gap-2 active:scale-95"
            >
              <i className="fa-solid fa-plus"></i>
              Create New Lab
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {topologies.length > 0 ? topologies.map((topo) => (
              <div 
                key={topo.name}
                className="group relative bg-slate-900 border border-slate-800 rounded-2xl p-6 hover:border-blue-500/50 hover:shadow-2xl hover:shadow-blue-900/10 transition-all cursor-default overflow-hidden"
              >
                <div className="absolute top-0 right-0 p-4 opacity-0 group-hover:opacity-100 transition-opacity">
                   <button 
                    onClick={(e) => { e.stopPropagation(); onDelete(topo.name); }}
                    className="w-8 h-8 rounded-lg bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white transition-all border border-red-500/20"
                   >
                     <i className="fa-solid fa-trash-can text-xs"></i>
                   </button>
                </div>

                <div className="w-12 h-12 bg-slate-800 rounded-xl flex items-center justify-center mb-4 text-slate-400 group-hover:bg-blue-600 group-hover:text-white transition-all border border-slate-700">
                  <i className="fa-solid fa-diagram-project"></i>
                </div>
                
                <h3 className="text-lg font-bold text-white mb-1 group-hover:text-blue-400 transition-colors">{topo.name}</h3>
                <div className="flex items-center gap-4 text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-6">
                   <span className="flex items-center gap-1.5"><i className="fa-solid fa-server"></i> {topo.nodes.length} Nodes</span>
                   <span className="flex items-center gap-1.5"><i className="fa-solid fa-link"></i> {topo.links.length} Links</span>
                </div>

                <div className="flex gap-2">
                  <button 
                    onClick={() => onSelect(topo)}
                    className="flex-1 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold rounded-lg border border-slate-700 transition-all"
                  >
                    Open Designer
                  </button>
                  <button className="w-10 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold rounded-lg border border-slate-700 transition-all flex items-center justify-center">
                    <i className="fa-solid fa-download"></i>
                  </button>
                </div>
              </div>
            )) : (
              <div className="col-span-full py-20 bg-slate-900/30 border-2 border-dashed border-slate-800 rounded-3xl flex flex-col items-center justify-center text-slate-600">
                 <i className="fa-solid fa-folder-open text-5xl mb-4 opacity-10"></i>
                 <h3 className="text-lg font-bold text-slate-400">Empty Workspace</h3>
                 <p className="text-sm max-w-xs text-center mt-1">Start your first journey by clicking 'Create New Lab' above.</p>
              </div>
            )}
          </div>
        </div>
      </main>

      <footer className="h-10 border-t border-slate-900 bg-slate-950 flex items-center px-10 justify-between text-[10px] text-slate-600 font-medium">
        <span>© 2024 Aura Visual Studio | Professional Edition</span>
        <div className="flex gap-4">
          <a href="#" className="hover:text-slate-400">Documentation</a>
          <a href="#" className="hover:text-slate-400">API Status</a>
          <a href="#" className="hover:text-slate-400">Support</a>
        </div>
      </footer>
    </div>
  );
};

export default Dashboard;
