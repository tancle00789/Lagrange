import { useCallback, useEffect, useState } from "react";
import {
  approveSkill,
  deprecateSkill,
  evaluateSkill,
  listApprovedSkills,
  listSkillPipeline,
} from "../api";
import type { EvalResult, SkillRecord } from "../types";

function skillKey(record: SkillRecord): string {
  return `${record.skill_id}@${record.version}`;
}

function SkillCard({
  record,
  evalResult,
  busy,
  onEvaluate,
  onApprove,
  onDeprecate,
}: {
  record: SkillRecord;
  evalResult?: EvalResult;
  busy: boolean;
  onEvaluate?: () => void;
  onApprove?: () => void;
  onDeprecate: () => void;
}) {
  const keywords = record.trigger_conditions.flatMap((c) => c.keywords);
  return (
    <li>
      <div className="meta">
        {record.name}{" "}
        <span className="id">
          {record.skill_id} v{record.version}
        </span>{" "}
        <span className={`status ${record.status}`}>{record.status}</span>
        {record.eval_pass_rate > 0 && (
          <span className="muted"> · pass {(record.eval_pass_rate * 100).toFixed(0)}%</span>
        )}
      </div>
      {record.description && <div className="snippet">{record.description}</div>}
      <div className="snippet">{record.procedure}</div>
      {keywords.length > 0 && <div className="meta muted">triggers: {keywords.join(", ")}</div>}
      {record.approved_by && <div className="meta muted">approved by {record.approved_by}</div>}
      {evalResult && (
        <div className={evalResult.eval_pass_rate >= 1 ? "grounded-yes" : "grounded-no"}>
          eval: {evalResult.passed_cases}/{evalResult.total_cases} passed
          {evalResult.regression_detected && " · regression vs baseline"}
          {evalResult.details
            .filter((d) => !d.passed)
            .map((d) => (
              <div key={d.case_id} className="error">
                {d.case_id}: {d.failures.join("; ")}
              </div>
            ))}
        </div>
      )}
      <div className="actions">
        {onEvaluate && (
          <button className="secondary" disabled={busy} onClick={onEvaluate}>
            {record.suggested_eval_cases.length > 0
              ? `Evaluate (${record.suggested_eval_cases.length} cases)`
              : "Evaluate"}
          </button>
        )}
        {onApprove && (
          <button disabled={busy || record.eval_pass_rate <= 0} onClick={onApprove}>
            Approve
          </button>
        )}
        <button className="danger" disabled={busy} onClick={onDeprecate}>
          Deprecate
        </button>
      </div>
    </li>
  );
}

export default function SkillsPanel() {
  const [approved, setApproved] = useState<SkillRecord[]>([]);
  const [testing, setTesting] = useState<SkillRecord[]>([]);
  const [candidates, setCandidates] = useState<SkillRecord[]>([]);
  const [evalResults, setEvalResults] = useState<Record<string, EvalResult>>({});
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [approvedList, testingList, candidateList] = await Promise.all([
        listApprovedSkills(),
        listSkillPipeline("testing"),
        listSkillPipeline("candidate"),
      ]);
      setApproved(approvedList);
      setTesting(testingList);
      setCandidates(candidateList);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = async (record: SkillRecord, fn: () => Promise<unknown>) => {
    const key = skillKey(record);
    setBusyKey(key);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey("");
    }
  };

  const evaluate = (record: SkillRecord) =>
    act(record, async () => {
      const response = await evaluateSkill(record.skill_id, record.version);
      setEvalResults((prev) => ({ ...prev, [skillKey(record)]: response.eval_result }));
    });

  const renderSection = (
    title: string,
    records: SkillRecord[],
    actions: { evaluate?: boolean; approve?: boolean },
  ) => (
    <div className="panel">
      <h2>
        {title} ({records.length})
      </h2>
      {records.length === 0 && <p className="muted">none</p>}
      <ul className="hits">
        {records.map((record) => (
          <SkillCard
            key={skillKey(record)}
            record={record}
            evalResult={evalResults[skillKey(record)]}
            busy={busyKey === skillKey(record)}
            onEvaluate={actions.evaluate ? () => void evaluate(record) : undefined}
            onApprove={
              actions.approve
                ? () => void act(record, () => approveSkill(record.skill_id, record.version))
                : undefined
            }
            onDeprecate={() =>
              void act(record, () => deprecateSkill(record.skill_id, record.version))
            }
          />
        ))}
      </ul>
    </div>
  );

  return (
    <div>
      <div className="panel">
        <h2>
          Skills{" "}
          <button className="link" onClick={() => void refresh()}>
            refresh
          </button>
        </h2>
        <p className="muted">
          Reusable procedures distilled from completed tasks. A candidate must pass a
          deterministic offline evaluation before it can be approved; only approved skills
          are injected into future answers. Distill from a completed task in the Tasks tab.
        </p>
        {error && <p className="error">{error}</p>}
      </div>
      {renderSection("Approved", approved, {})}
      {renderSection("Testing (awaiting approval)", testing, {
        evaluate: true,
        approve: true,
      })}
      {renderSection("Candidates", candidates, { evaluate: true })}
    </div>
  );
}
