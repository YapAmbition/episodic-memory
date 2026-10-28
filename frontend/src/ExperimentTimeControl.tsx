import { useEffect, useState } from "react";
import { Clock3, RefreshCw } from "lucide-react";
import { localInput } from "./api";

export type ExperimentClock = {
  mode: "realtime" | "simulated";
  at: string;
};

type Props = {
  clock: ExperimentClock;
  disabled: boolean;
  onChange: (clock: ExperimentClock) => void;
};

export function ExperimentTimeControl({ clock, disabled, onChange }: Props) {
  // Only this display ticks; requests and their cancellation depend on mode changes.
  const [now, setNow] = useState(() => new Date().toISOString());
  useEffect(() => {
    if (clock.mode !== "realtime") return;
    const tick = () => setNow(new Date().toISOString());
    tick();
    const interval = window.setInterval(tick, 1000);
    return () => window.clearInterval(interval);
  }, [clock.mode]);

  const realtime = clock.mode === "realtime";
  const useRealtime = () =>
    onChange({ mode: "realtime", at: new Date().toISOString() });

  return (
    <div className="toolbar-group time-control">
      <Clock3 size={15} />
      <span>实验时间</span>
      <div className="time-mode-switch" role="group" aria-label="时间模式">
        <button
          type="button"
          aria-pressed={realtime}
          disabled={disabled}
          onClick={useRealtime}
        >
          实时
        </button>
        <button
          type="button"
          aria-pressed={!realtime}
          disabled={disabled}
          onClick={() =>
            onChange({
              mode: "simulated",
              at: realtime ? new Date().toISOString() : clock.at,
            })
          }
        >
          模拟
        </button>
      </div>
      {realtime ? (
        <div className="live-clock" title="每次操作自动使用当前时间">
          <i aria-hidden="true" />
          <time dateTime={now} aria-label="当前时间">
            {new Date(now).toLocaleString("zh-CN", {
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
              hour12: false,
            })}
          </time>
        </div>
      ) : (
        <div className="simulated-clock">
          <input
            type="datetime-local"
            aria-label="实验时间"
            value={localInput(clock.at)}
            disabled={disabled}
            onChange={(event) => {
              if (event.target.value && Number.isFinite(new Date(event.target.value).getTime()))
                onChange({
                  mode: "simulated",
                  at: new Date(event.target.value).toISOString(),
                });
            }}
          />
          <button
            type="button"
            className="time-step"
            disabled={disabled}
            onClick={() =>
              onChange({
                mode: "simulated",
                at: new Date(new Date(clock.at).getTime() + 30 * 86400000).toISOString(),
              })
            }
          >
            +30 天
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label="恢复当前时间"
            title="恢复当前时间并切回实时模式"
            disabled={disabled}
            onClick={useRealtime}
          >
            <RefreshCw size={13} />
          </button>
        </div>
      )}
    </div>
  );
}
