import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArchetypeIcon } from "../components/icons";
import AdminMenuButton from "../components/AdminMenuButton";
import { useUser } from "../contexts/UserContext";
import { canManageUsers } from "../utils/permissions";
import {
  apiRequest,
  createSupportBundle,
  getSupportBundle,
  listSupportBundles,
  type SupportBundle,
} from "../api";
import { downloadBlob } from "../utils/download";

type LabOption = { id: string; name: string };
type AgentOption = { id: string; name: string };

const DEFAULT_WINDOW_HOURS = 24;

export default function SupportBundlesPage() {
  const navigate = useNavigate();
  const { user, loading: userLoading } = useUser();
  const [labs, setLabs] = useState<LabOption[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [history, setHistory] = useState<SupportBundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<string | null>(null);
  const [activeBundle, setActiveBundle] = useState<SupportBundle | null>(null);

  const [summary, setSummary] = useState("");
  const [reproSteps, setReproSteps] = useState("");
  const [expectedBehavior, setExpectedBehavior] = useState("");
  const [actualBehavior, setActualBehavior] = useState("");
  const [timeWindowHours, setTimeWindowHours] = useState(DEFAULT_WINDOW_HOURS);
  const [selectedLabs, setSelectedLabs] = useState<string[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [includeConfigs, setIncludeConfigs] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const canAccess = canManageUsers(user ?? null);

  useEffect(() => {
    if (!userLoading && !canAccess) {
      navigate("/", { replace: true });
    }
  }, [userLoading, canAccess, navigate]);

  async function loadData() {
    setLoading(true);
    try {
      const [labResponse, agentResponse, bundleResponse] = await Promise.all([
        apiRequest<{ labs: LabOption[] }>("/labs"),
        apiRequest<AgentOption[]>("/agents"),
        listSupportBundles(30),
      ]);
      setLabs(labResponse.labs || []);
      setAgents(agentResponse || []);
      setHistory(bundleResponse || []);
      setStatus(null);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Failed to load support bundle data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (canAccess) {
      void loadData();
    }
  }, [canAccess]);

  useEffect(() => {
    if (!activeBundle) return;
    if (!["pending", "running"].includes(activeBundle.status)) return;

    const id = window.setInterval(async () => {
      try {
        const updated = await getSupportBundle(activeBundle.id);
        setActiveBundle(updated);
        if (updated.status === "completed" || updated.status === "failed") {
          window.clearInterval(id);
          void loadData();
        }
      } catch {
        window.clearInterval(id);
      }
    }, 3000);

    return () => window.clearInterval(id);
  }, [activeBundle]);

  const canSubmit = useMemo(() => {
    return (
      summary.trim().length >= 5 &&
      reproSteps.trim().length >= 5 &&
      expectedBehavior.trim().length >= 2 &&
      actualBehavior.trim().length >= 2 &&
      !submitting
    );
  }, [summary, reproSteps, expectedBehavior, actualBehavior, submitting]);

  async function submitBundle() {
    if (!canSubmit) return;
    setSubmitting(true);
    setStatus(null);
    try {
      const created = await createSupportBundle({
        summary: summary.trim(),
        repro_steps: reproSteps.trim(),
        expected_behavior: expectedBehavior.trim(),
        actual_behavior: actualBehavior.trim(),
        time_window_hours: Math.max(1, Math.min(168, timeWindowHours)),
        impacted_lab_ids: selectedLabs,
        impacted_agent_ids: selectedAgents,
        include_configs: includeConfigs,
        pii_safe: true,
      });
      setActiveBundle(created);
      setStatus("Support bundle generation started.");
      setSummary("");
      setReproSteps("");
      setExpectedBehavior("");
      setActualBehavior("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Failed to create support bundle");
    } finally {
      setSubmitting(false);
    }
  }

  async function downloadBundle(bundleId: string) {
    try {
      const token = localStorage.getItem("token");
      const response = await fetch(`/api/support-bundles/${bundleId}/download`, {
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });
      if (!response.ok) {
        throw new Error(`Download failed: HTTP ${response.status}`);
      }
      const blob = await response.blob();
      downloadBlob(blob, `archetype_support_bundle_${bundleId}.zip`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Failed to download bundle");
    }
  }

  function toggleListSelection(current: string[], value: string): string[] {
    return current.includes(value)
      ? current.filter((item) => item !== value)
      : [...current, value];
  }

  return (
    <div className="min-h-screen bg-stone-50/72 dark:bg-stone-900/72 backdrop-blur-[1px] text-stone-700 dark:text-stone-200">
      <header className="h-20 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10">
        <div className="flex items-center gap-4">
          <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
          <div>
            <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">SUPPORT BUNDLES</h1>
            <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Offline Troubleshooting Export</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate("/")}
            className="flex items-center gap-2 px-3 py-2 glass-control text-stone-700 dark:text-stone-100 rounded-lg transition-all"
            title="Back to workspace"
          >
            <i className="fa-solid fa-arrow-left text-xs"></i>
            <span className="text-[10px] font-bold uppercase">Back</span>
          </button>
          <AdminMenuButton />
        </div>
      </header>

      <main className="max-w-6xl mx-auto p-8 space-y-6">
        <section className="glass-surface-elevated rounded-2xl p-6">
          <h2 className="text-base font-bold uppercase tracking-wide text-stone-800 dark:text-stone-100">Create Bundle</h2>
          <p className="text-xs text-stone-500 dark:text-stone-400 mt-2">
            Super-admin only. Offline zip, 200MB cap, PII-safe mode enabled, host/lab names masked.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            <label className="text-xs font-semibold">
              Summary
              <input
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                className="mt-1 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
                placeholder="Short issue summary"
              />
            </label>
            <label className="text-xs font-semibold">
              Time Window (hours, max 168)
              <input
                type="number"
                value={timeWindowHours}
                min={1}
                max={168}
                onChange={(e) => setTimeWindowHours(Number(e.target.value))}
                className="mt-1 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
              />
            </label>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            <label className="text-xs font-semibold">
              Repro Steps
              <textarea
                value={reproSteps}
                onChange={(e) => setReproSteps(e.target.value)}
                rows={5}
                className="mt-1 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
              />
            </label>
            <label className="text-xs font-semibold">
              Expected Behavior
              <textarea
                value={expectedBehavior}
                onChange={(e) => setExpectedBehavior(e.target.value)}
                rows={5}
                className="mt-1 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
              />
            </label>
          </div>

          <label className="text-xs font-semibold block mt-4">
            Actual Behavior
            <textarea
              value={actualBehavior}
              onChange={(e) => setActualBehavior(e.target.value)}
              rows={4}
              className="mt-1 w-full rounded-lg border border-stone-300 dark:border-stone-700 bg-white dark:bg-stone-800 px-3 py-2 text-sm"
            />
          </label>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            <div>
              <p className="text-xs font-semibold mb-1">Impacted Labs</p>
              <div className="max-h-32 overflow-auto rounded-lg border border-stone-200 dark:border-stone-700 p-2 text-xs">
                {labs.map((lab) => (
                  <label key={lab.id} className="flex items-center gap-2 py-1">
                    <input
                      type="checkbox"
                      checked={selectedLabs.includes(lab.id)}
                      onChange={() => setSelectedLabs((prev) => toggleListSelection(prev, lab.id))}
                    />
                    <span>{lab.name}</span>
                  </label>
                ))}
                {labs.length === 0 && <p className="text-stone-500">No labs available</p>}
              </div>
            </div>
            <div>
              <p className="text-xs font-semibold mb-1">Impacted Agents</p>
              <div className="max-h-32 overflow-auto rounded-lg border border-stone-200 dark:border-stone-700 p-2 text-xs">
                {agents.map((agent) => (
                  <label key={agent.id} className="flex items-center gap-2 py-1">
                    <input
                      type="checkbox"
                      checked={selectedAgents.includes(agent.id)}
                      onChange={() => setSelectedAgents((prev) => toggleListSelection(prev, agent.id))}
                    />
                    <span>{agent.name}</span>
                  </label>
                ))}
                {agents.length === 0 && <p className="text-stone-500">No agents available</p>}
              </div>
            </div>
          </div>

          <label className="mt-4 inline-flex items-center gap-2 text-xs font-semibold">
            <input
              type="checkbox"
              checked={includeConfigs}
              onChange={(e) => setIncludeConfigs(e.target.checked)}
            />
            Include raw config snapshots (opt-in)
          </label>

          <div className="mt-4 flex items-center gap-3">
            <button
              disabled={!canSubmit}
              onClick={submitBundle}
              className="px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wide bg-sage-600 text-white disabled:bg-stone-400 disabled:cursor-not-allowed"
            >
              {submitting ? "Generating..." : "Generate Bundle"}
            </button>
            {status && <span className="text-xs text-stone-600 dark:text-stone-300">{status}</span>}
          </div>
        </section>

        <section className="glass-surface-elevated rounded-2xl p-6">
          <h2 className="text-base font-bold uppercase tracking-wide text-stone-800 dark:text-stone-100">Recent Bundles (7 Days)</h2>
          {loading ? (
            <p className="text-sm text-stone-500 mt-3">Loading...</p>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left border-b border-stone-200 dark:border-stone-700">
                    <th className="py-2">Created</th>
                    <th className="py-2">Status</th>
                    <th className="py-2">Size</th>
                    <th className="py-2">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((bundle) => (
                    <tr key={bundle.id} className="border-b border-stone-100 dark:border-stone-800">
                      <td className="py-2">{new Date(bundle.created_at).toLocaleString()}</td>
                      <td className="py-2">{bundle.status}</td>
                      <td className="py-2">{bundle.size_bytes ? `${Math.round(bundle.size_bytes / 1024 / 1024)} MB` : "-"}</td>
                      <td className="py-2">
                        {bundle.status === "completed" ? (
                          <button
                            onClick={() => void downloadBundle(bundle.id)}
                            className="text-sage-600 dark:text-sage-400 font-semibold"
                          >
                            Download
                          </button>
                        ) : (
                          <span className="text-stone-500">{bundle.error_message || "-"}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {history.length === 0 && (
                    <tr>
                      <td className="py-3 text-stone-500" colSpan={4}>No support bundles generated yet.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
