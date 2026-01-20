
import React, { useState } from 'react';

interface AuthProps {
  onLogin: (username: string) => void;
}

const Auth: React.FC<AuthProps> = ({ onLogin }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (username.trim()) {
      onLogin(username);
    }
  };

  return (
    <div className="min-h-screen bg-white dark:bg-slate-950 flex items-center justify-center p-6 bg-gradient-animate transition-colors duration-700">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,#3b82f6,transparent)] opacity-5 dark:opacity-20"></div>
      
      <div className="w-full max-w-md relative animate-in fade-in slide-in-from-bottom-4 duration-700">
        <div className="flex flex-col items-center mb-10">
          <div className="w-16 h-16 bg-blue-600 rounded-2xl flex items-center justify-center shadow-2xl shadow-blue-500/20 mb-4 border border-blue-400/30">
            <i className="fa-solid fa-bolt-lightning text-white text-3xl"></i>
          </div>
          <h1 className="text-4xl font-black text-slate-900 dark:text-white tracking-tighter uppercase">AURA</h1>
          <p className="text-blue-600 dark:text-blue-500 text-[10px] font-bold tracking-[0.3em] uppercase mt-1">Visual Studio</p>
        </div>

        <div className="bg-white/60 dark:bg-slate-900/50 backdrop-blur-xl border border-slate-200 dark:border-slate-800 p-8 rounded-3xl shadow-2xl">
          <form onSubmit={handleSubmit} className="space-y-6">
            <div className="space-y-2">
              <label className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest ml-1">Identity</label>
              <div className="relative">
                <i className="fa-solid fa-user absolute left-4 top-1/2 -translate-y-1/2 text-slate-400 text-xs"></i>
                <input
                  type="text"
                  placeholder="Username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl py-3 pl-11 pr-4 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-all"
                />
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest ml-1">Credential</label>
              <div className="relative">
                <i className="fa-solid fa-lock absolute left-4 top-1/2 -translate-y-1/2 text-slate-400 text-xs"></i>
                <input
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl py-3 pl-11 pr-4 text-sm text-slate-900 dark:text-white focus:outline-none focus:border-blue-500 transition-all"
                />
              </div>
            </div>

            <button
              type="submit"
              className="w-full py-3 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl shadow-lg shadow-blue-900/20 transition-all active:scale-[0.98] mt-4"
            >
              Sign In to Aura
            </button>
          </form>
        </div>

        <p className="text-center mt-8 text-slate-400 dark:text-slate-500 text-[11px] font-bold uppercase tracking-widest">
          v2.4.0-STABLE | IAAS EXCELLENCE
        </p>
      </div>
    </div>
  );
};

export default Auth;
