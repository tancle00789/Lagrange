import { useEffect, useState } from "react";
import {
  cancelTask,
  distillSkill,
  downloadArtifact,
  getTask,
  resumeTask,
  subscribeTaskEvents,
} from "../api";
import { sourceId, type TaskStatus } from "../types";

interface Props {
  task: TaskStatus;
  onChanged: () => void;
}

interface TimelineEntry {
  type: string;
  data: string;
}

const ACTIVE = new Set(["pending", "running"]);

export default function TaskDetail({ task, onChanged }: Props) {
  const [current, setCurrent] = useState<TaskStatus>(task);
  const [events, setEvents] = useState<TimelineEntry[]>([]);
  const [error, setError] = useState("");
  const [distilling, setDistilling] = useState(false);
  const [distillNote, setDistillNote] = useState("");

  useEffect(() => {
    setCurrent(task);
    setEvents([]);
    const close = subscribeTaskEvents(task.task_id, (type, data) => {
      setEvents((prev) => [...prev.slice(-199), { type, data }]);
      if (type !== "snapshot" && type !== "model_called") {
        void getTask(task.task_id).then(setCurrent).catch(() => undefined);
      }
    });
    return close;
  }, [task]);

  const act = async (fn: () => Promise<TaskStatus>) => {
    setError("");
    try {
      setCurrent(await fn());
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const distill = async () => {
    setDistilling(true);
    setDistillNote("");
    setError("");
    try {
      const response = await distillSkill(current.task_id);
      if (response.worth_saving && response.record) {
        const record = response.record;
        setDistillNote(
          `已创建候选技能 ${record.skill_id} v${record.version}` +
            (response.eval_cases.length > 0
              ? `（附带 ${response.eval_cases.length} 条建议评估用例）`
              : "") +
            "——见「技能」页签",
        );
      } else {
        setDistillNote("模型判断该任务不具备可复用性（未保存技能）");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDistilling(false);
    }
  };

  const report = current.report;
  const validChunkIds = new Set(current.knowledge_hits.map((h) => h.chunk_id));
  for (const record of current.external_sources) {
    validChunkIds.add(sourceId(record));
  }

  return (
    <div>
      <div className="panel">
        <h2>
          任务 <span className="id">{current.task_id}</span>{" "}
          <span className={`status ${current.status}`}>{current.status}</span>
        </h2>
        {(ACTIVE.has(current.status) ||
          current.status === "paused" ||
          current.status === "completed") && (
          <div className="actions">
            {current.status === "paused" && (
              <>
                <button onClick={() => void act(() => resumeTask(current.task_id, true))}>
                  批准
                </button>
                <button
                  className="secondary"
                  onClick={() => void act(() => resumeTask(current.task_id, false, "rejected"))}
                >
                  拒绝
                </button>
              </>
            )}
            {ACTIVE.has(current.status) && (
              <button
                className="danger"
                onClick={() => void act(() => cancelTask(current.task_id))}
              >
                取消
              </button>
            )}
            {current.status === "completed" && (
              <button className="secondary" disabled={distilling} onClick={() => void distill()}>
                {distilling ? "蒸馏中……" : "蒸馏技能"}
              </button>
            )}
          </div>
        )}
        {distillNote && <p className="muted">{distillNote}</p>}
        {error && <p className="error">{error}</p>}
      </div>

      <div className="panel">
        <h2>事件时间线</h2>
        {events.length === 0 && <p className="muted">等待事件……</p>}
        <ul className="timeline">
          {events.map((entry, i) => (
            <li key={i}>
              <span className="etype">{entry.type}</span>
              {entry.data.slice(0, 160)}
            </li>
          ))}
        </ul>
      </div>

      <div className="panel">
        <h2>知识库命中（{current.knowledge_hits.length}）</h2>
        <ul className="hits">
          {current.knowledge_hits.map((hit) => (
            <li key={hit.chunk_id}>
              <div className="meta">
                {hit.chunk_id} · score {hit.score.toFixed(3)} · {hit.source.title}
              </div>
              <div className="snippet">{hit.snippet}</div>
            </li>
          ))}
        </ul>
      </div>

      {current.external_sources.length > 0 && (
        <div className="panel">
          <h2>外部来源（{current.external_sources.length}）</h2>
          <ul className="hits">
            {current.external_sources.map((record) => (
              <li key={sourceId(record)}>
                <div className="meta">
                  {sourceId(record)} · {record.venue || record.source_type}
                  {record.published ? ` · ${record.published}` : ""}
                  {typeof record.extra.knowledge_id === "string" && (
                    <span className="grounded-yes"> · 已灌库</span>
                  )}
                </div>
                <div>
                  <a href={record.url} target="_blank" rel="noreferrer">
                    {record.title}
                  </a>
                  {record.authors.length > 0 && (
                    <span className="muted"> — {record.authors.slice(0, 3).join(", ")}</span>
                  )}
                </div>
                {record.abstract && <div className="snippet">{record.abstract}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {current.model_runs.length > 0 && (
        <div className="panel">
          <h2>模型运行（{current.model_runs.length}）</h2>
          <ul className="hits">
            {current.model_runs.map((run, i) => (
              <li key={run.run_id || i}>
                <div className="meta">
                  {run.block_name} ·{" "}
                  <span className={run.status === "completed" ? "grounded-yes" : "grounded-no"}>
                    {run.status}
                  </span>
                  {run.run_id && <span className="muted"> · {run.run_id.slice(0, 12)}</span>}
                </div>
                {run.error && <div className="error">{run.error}</div>}
                {Object.keys(run.metrics).length > 0 && (
                  <div className="snippet">
                    {Object.entries(run.metrics)
                      .map(([key, value]) => `${key}=${Number(value.toFixed(4))}`)
                      .join(" · ")}
                  </div>
                )}
                {run.artifact_ids.length > 0 && (
                  <div className="actions">
                    {run.artifact_ids.map((id) => (
                      <button
                        key={id}
                        className="secondary"
                        onClick={() => void downloadArtifact(id).catch(() => undefined)}
                      >
                        {id.split("/").pop()}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report && (
        <div className="panel">
          <h2>报告</h2>
          <p className="answer">{report.answer}</p>
          <p>
            有据可依：{" "}
            <span className={report.grounded ? "grounded-yes" : "grounded-no"}>
              {report.grounded ? "是" : "否"}
            </span>
            {report.dropped_citations > 0 && (
              <span className="muted"> · {report.dropped_citations} 条引用被丢弃</span>
            )}
          </p>
          {report.notes.length > 0 && (
            <p className="muted">备注:{report.notes.join(";")}</p>
          )}
          <ul className="claims">
            {report.claims.map((claim, i) => (
              <li key={i}>
                <div>{claim.text}</div>
                <div className="chunk-ids">
                  {claim.chunk_ids.map((id) => (
                    <span key={id} style={{ color: validChunkIds.has(id) ? "#3fb27f" : "#e05656" }}>
                      {id}{" "}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
