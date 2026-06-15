import React, { useState, useEffect, useCallback } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from 'recharts';
import { apiFetch } from '../../../api/client';

interface UsageSummary {
  total_jobs: number;
  total_cost_cents: number;
  total_compute_seconds: number;
  total_artifact_bytes: number;
}

interface DailyItem {
  day: string;
  job_count: number;
  cost_cents: number;
  compute_seconds: number;
}

interface FormatItem {
  export_format: string;
  job_count: number;
  cost_cents: number;
  compute_seconds: number;
}

interface RecentJob {
  created_at: string;
  export_format: string;
  status: string;
  compute_duration_seconds: number;
  artifact_byte_size: number;
  cost_cents: number;
  username: string | null;
}

const FORMAT_COLORS: Record<string, string> = {
  stl: '#6366f1',
  step: '#8b5cf6',
  gltf: '#ec4899',
  glb: '#f59e0b',
};

const FALLBACK_COLORS = ['#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981'];

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(2)}h`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export const UsageTab: React.FC<{ serverUrl: string; getAccessToken: () => Promise<string> }> = ({
  serverUrl,
  getAccessToken,
}) => {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [daily, setDaily] = useState<DailyItem[]>([]);
  const [formatBreakdown, setFormatBreakdown] = useState<FormatItem[]>([]);
  const [recent, setRecent] = useState<RecentJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getAccessToken();
      const [summaryRes, dailyRes, formatRes, recentRes] = await Promise.all([
        apiFetch(`${serverUrl}/usage/summary`, async () => token),
        apiFetch(`${serverUrl}/usage/daily?days=30`, async () => token),
        apiFetch(`${serverUrl}/usage/by-format`, async () => token),
        apiFetch(`${serverUrl}/usage/recent?limit=50`, async () => token),
      ]);

      if (!summaryRes.ok) {
        if (summaryRes.status === 403) {
          setError('hidden');
          return;
        }
        setError('Failed to load usage data');
        return;
      }

      const [summaryData, dailyData, formatData, recentData] = await Promise.all([
        summaryRes.json(),
        dailyRes.json(),
        formatRes.json(),
        recentRes.json(),
      ]);

      setSummary(summaryData);
      setDaily(dailyData);
      setFormatBreakdown(formatData);
      setRecent(recentData);
    } catch {
      setError('Failed to load usage data');
    } finally {
      setLoading(false);
    }
  }, [serverUrl, getAccessToken]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  if (error === 'hidden') return null;
  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400">
        Loading usage data...
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-400">
        <p>{error}</p>
        <button onClick={() => void fetchData()} className="px-3 py-1 bg-slate-800 border border-slate-700 rounded text-sm hover:text-slate-200">
          Retry
        </button>
      </div>
    );
  }

  const chartData = daily.map((d) => ({
    ...d,
    dayLabel: d.day.slice(0, 10),
    cost_dollars: d.cost_cents / 100,
    compute_hours: d.compute_seconds / 3600,
  }));

  return (
    <div className="flex flex-col h-full overflow-auto p-4 gap-4 bg-slate-900">
      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-4 gap-3">
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <div className="text-xs text-slate-400 uppercase tracking-wide">Total Jobs</div>
            <div className="text-2xl font-bold text-slate-100 mt-1">{summary.total_jobs}</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <div className="text-xs text-slate-400 uppercase tracking-wide">Total Cost</div>
            <div className="text-2xl font-bold text-emerald-400 mt-1">{formatCents(summary.total_cost_cents)}</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <div className="text-xs text-slate-400 uppercase tracking-wide">Compute Time</div>
            <div className="text-2xl font-bold text-indigo-400 mt-1">{formatDuration(summary.total_compute_seconds)}</div>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <div className="text-xs text-slate-400 uppercase tracking-wide">Artifact Data</div>
            <div className="text-2xl font-bold text-purple-400 mt-1">{formatBytes(summary.total_artifact_bytes)}</div>
          </div>
        </div>
      )}

      {/* Charts Row */}
      <div className="grid grid-cols-2 gap-4 min-h-0">
        {/* Daily Cost Chart */}
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3">Daily Cost (last 30 days)</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="dayLabel" tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid #475569', borderRadius: '6px' }}
                labelStyle={{ color: '#e2e8f0' }}
                formatter={(value: number) => [`$${value.toFixed(2)}`, 'Cost']}
              />
              <Bar dataKey="cost_dollars" fill="#6366f1" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Format Breakdown */}
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3">Cost by Format</h3>
          {formatBreakdown.length === 0 ? (
            <div className="text-slate-500 text-sm text-center mt-16">No data yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={formatBreakdown}
                  dataKey="cost_cents"
                  nameKey="export_format"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  label={({ export_format, cost_cents }: FormatItem) =>
                    `${export_format.toUpperCase()} ${formatCents(cost_cents)}`
                  }
                  labelLine={false}
                >
                  {formatBreakdown.map((entry, index) => (
                    <Cell
                      key={entry.export_format}
                      fill={FORMAT_COLORS[entry.export_format] || FALLBACK_COLORS[index % FALLBACK_COLORS.length]}
                    />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#1e293b', border: '1px solid #475569', borderRadius: '6px' }}
                  formatter={(value: number) => [formatCents(value), 'Cost']}
                />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Recent Jobs Table */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 flex-1 min-h-0 overflow-auto">
        <h3 className="text-sm font-semibold text-slate-300 mb-3">Recent Jobs</h3>
        {recent.length === 0 ? (
          <div className="text-slate-500 text-sm text-center mt-8">No jobs yet</div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-400 border-b border-slate-700">
                <th className="text-left py-2 px-2">Date</th>
                <th className="text-left py-2 px-2">User</th>
                <th className="text-left py-2 px-2">Format</th>
                <th className="text-left py-2 px-2">Status</th>
                <th className="text-right py-2 px-2">Duration</th>
                <th className="text-right py-2 px-2">Size</th>
                <th className="text-right py-2 px-2">Cost</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((job, i) => (
                <tr key={i} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                  <td className="py-2 px-2 text-slate-300 font-mono">
                    {new Date(job.created_at).toLocaleDateString()}
                  </td>
                  <td className="py-2 px-2 text-slate-400">{job.username || '-'}</td>
                  <td className="py-2 px-2">
                    <span className="px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 font-mono uppercase">
                      {job.export_format}
                    </span>
                  </td>
                  <td className="py-2 px-2">
                    <span
                      className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                        job.status === 'succeeded'
                          ? 'bg-emerald-900/50 text-emerald-400'
                          : 'bg-red-900/50 text-red-400'
                      }`}
                    >
                      {job.status}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-right text-slate-400 font-mono">
                    {formatDuration(job.compute_duration_seconds)}
                  </td>
                  <td className="py-2 px-2 text-right text-slate-400 font-mono">
                    {formatBytes(job.artifact_byte_size)}
                  </td>
                  <td className="py-2 px-2 text-right text-emerald-400 font-mono">
                    {formatCents(job.cost_cents)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
