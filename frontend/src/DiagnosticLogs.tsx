import { useEffect, useState } from "react";
import { Download, FileText, RefreshCw } from "lucide-react";
import { api, downloadDiagnosticLogs } from "./api";

type Snapshot = { available: boolean; level?: string; write_failed?: boolean; text: string };
export function DiagnosticLogs() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function refresh() {
    setBusy(true); setError("");
    try { setData(await api<Snapshot>("/diagnostics/logs")); }
    catch (e) { setError(e instanceof Error ? e.message : "读取诊断日志失败"); }
    finally { setBusy(false); }
  }
  useEffect(() => { void refresh(); }, []);
  if (data && !data.available) return null;
  return <section className="panel monitor-panel diagnostic-logs">
    <div className="diagnostic-log-heading">
      <div><h3><FileText size={18} />插件诊断日志</h3>
        <p>最近 200 条 · {data?.level || "INFO"} · 手动刷新，保留你的阅读位置</p></div>
      <div className="diagnostic-log-actions">
        <button disabled={busy} onClick={() => void refresh()}><RefreshCw size={15} />刷新</button>
        <button disabled={busy || !data?.available} onClick={async () => {
          setBusy(true); setError("");
          try { await downloadDiagnosticLogs(); }
          catch (e) { setError(e instanceof Error ? e.message : "下载失败"); }
          finally { setBusy(false); }
        }}><Download size={15} />下载日志</button>
      </div>
    </div>
    {error && <p role="alert">{error}</p>}
    {data?.write_failed && <p role="alert">日志文件读写异常，请检查插件数据目录权限及剩余磁盘空间。聊天与记忆功能不受日志故障阻断。</p>}
    <details><summary>展开最近日志</summary><pre tabIndex={0}>{data?.text || "暂无日志。升级前的异常无法追溯，之后发生的诊断会记录在这里。"}</pre></details>
    <p className="diagnostic-log-note">下载包含轮转日志，最多约 25 MB。不含聊天正文或密钥；诊断信息包含内部任务与空间 ID。日志级别在 AstrBot 插件配置中调整，重载后生效。</p>
  </section>;
}
