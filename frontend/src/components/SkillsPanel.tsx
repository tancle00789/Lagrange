import { useCallback, useEffect, useState } from "react";
import {
  approveSkill,
  deprecateSkill,
  evaluateSkill,
  listApprovedSkills,
  listSkillPipeline,
} from "../api";
import type { EvalCase, EvalResult, SkillRecord } from "../types";

function skillKey(record: SkillRecord): string {
  return `${record.skill_id}@${record.version}`;
}

function blankCase(skillId: string, index: number): EvalCase {
  return {
    case_id: `case-${index + 1}`,
    query: "",
    expected_answer_contains: [],
    expect_skill_matched: skillId,
    expect_model_blocks: [],
  };
}

function EvalCaseEditor({
  skillId,
  cases,
  onChange,
}: {
  skillId: string;
  cases: EvalCase[];
  onChange: (cases: EvalCase[]) => void;
}) {
  const update = (index: number, patch: Partial<EvalCase>) => {
    onChange(cases.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  };
  return (
    <div className="case-editor">
      {cases.map((evalCase, index) => (
        <div key={index} className="case-row">
          <input
            type="text"
            placeholder="用户可能提出的查询"
            value={evalCase.query}
            onChange={(e) => update(index, { query: e.target.value })}
          />
          <textarea
            rows={2}
            placeholder="期望答案包含的关键词,每行一条"
            value={evalCase.expected_answer_contains.join("\n")}
            onChange={(e) =>
              update(index, {
                expected_answer_contains: e.target.value
                  .split("\n")
                  .map((line) => line.trim())
                  .filter(Boolean),
              })
            }
          />
          <button
            className="link"
            onClick={() => onChange(cases.filter((_, i) => i !== index))}
          >
            删除
          </button>
        </div>
      ))}
      <button
        className="link"
        disabled={cases.length >= 20}
        onClick={() => onChange([...cases, blankCase(skillId, cases.length)])}
      >
        + 添加用例
      </button>
    </div>
  );
}

function SkillCard({
  record,
  evalResult,
  busy,
  editorOpen,
  draftCases,
  onToggleEditor,
  onDraftChange,
  onEvaluate,
  onApprove,
  onDeprecate,
}: {
  record: SkillRecord;
  evalResult?: EvalResult;
  busy: boolean;
  editorOpen: boolean;
  draftCases?: EvalCase[];
  onToggleEditor?: () => void;
  onDraftChange?: (cases: EvalCase[]) => void;
  onEvaluate?: () => void;
  onApprove?: () => void;
  onDeprecate: () => void;
}) {
  const keywords = record.trigger_conditions.flatMap((c) => c.keywords);
  const caseCount = draftCases?.length ?? record.suggested_eval_cases.length;
  return (
    <li>
      <div className="meta">
        {record.name}{" "}
        <span className="id">
          {record.skill_id} v{record.version}
        </span>{" "}
        <span className={`status ${record.status}`}>{record.status}</span>
        {record.eval_pass_rate > 0 && (
          <span className="muted"> · 通过率 {(record.eval_pass_rate * 100).toFixed(0)}%</span>
        )}
      </div>
      {record.description && <div className="snippet">{record.description}</div>}
      <div className="snippet">{record.procedure}</div>
      {keywords.length > 0 && <div className="meta muted">触发词:{keywords.join(",")}</div>}
      {record.approved_by && <div className="meta muted">审批人:{record.approved_by}</div>}
      {evalResult && (
        <div className={evalResult.eval_pass_rate >= 1 ? "grounded-yes" : "grounded-no"}>
          评估:{evalResult.passed_cases}/{evalResult.total_cases} 通过
          {evalResult.regression_detected && " · 相对基线有回退"}
          {evalResult.details
            .filter((d) => !d.passed)
            .map((d) => (
              <div key={d.case_id} className="error">
                {d.case_id}: {d.failures.join("; ")}
              </div>
            ))}
        </div>
      )}
      {editorOpen && draftCases && onDraftChange && (
        <EvalCaseEditor
          skillId={record.skill_id}
          cases={draftCases}
          onChange={onDraftChange}
        />
      )}
      <div className="actions">
        {onToggleEditor && (
          <button className="link" onClick={onToggleEditor}>
            {editorOpen ? "收起用例" : `评估用例 (${caseCount})`}
          </button>
        )}
        {onEvaluate && (
          <button className="secondary" disabled={busy} onClick={onEvaluate}>
            评估{caseCount > 0 ? ` (${caseCount})` : ""}
          </button>
        )}
        {onApprove && (
          <button disabled={busy || record.eval_pass_rate <= 0} onClick={onApprove}>
            批准
          </button>
        )}
        <button className="danger" disabled={busy} onClick={onDeprecate}>
          弃用
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
  const [drafts, setDrafts] = useState<Record<string, EvalCase[]>>({});
  const [openEditors, setOpenEditors] = useState<Record<string, boolean>>({});
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

  const toggleEditor = (record: SkillRecord) => {
    const key = skillKey(record);
    setOpenEditors((prev) => ({ ...prev, [key]: !prev[key] }));
    // Seed the draft from the distill-suggested cases on first open.
    setDrafts((prev) =>
      prev[key] !== undefined
        ? prev
        : {
            ...prev,
            [key]:
              record.suggested_eval_cases.length > 0
                ? record.suggested_eval_cases.map((c) => ({ ...c }))
                : [blankCase(record.skill_id, 0)],
          },
    );
  };

  const evaluate = (record: SkillRecord) =>
    act(record, async () => {
      const key = skillKey(record);
      const draft = drafts[key];
      // An edited draft overrides the backend's suggested-case fallback.
      const cases = draft?.filter((c) => c.query.trim());
      if (draft !== undefined && (cases === undefined || cases.length === 0)) {
        throw new Error("请至少添加一条带查询的评估用例");
      }
      const response = await evaluateSkill(record.skill_id, record.version, cases);
      setEvalResults((prev) => ({ ...prev, [key]: response.eval_result }));
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
      {records.length === 0 && <p className="muted">无</p>}
      <ul className="hits">
        {records.map((record) => (
          <SkillCard
            key={skillKey(record)}
            record={record}
            evalResult={evalResults[skillKey(record)]}
            busy={busyKey === skillKey(record)}
            editorOpen={openEditors[skillKey(record)] ?? false}
            draftCases={drafts[skillKey(record)]}
            onToggleEditor={actions.evaluate ? () => toggleEditor(record) : undefined}
            onDraftChange={
              actions.evaluate
                ? (cases) => setDrafts((prev) => ({ ...prev, [skillKey(record)]: cases }))
                : undefined
            }
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
          技能{" "}
          <button className="link" onClick={() => void refresh()}>
            刷新
          </button>
        </h2>
        <p className="muted">
          从已完成任务中蒸馏出的可复用流程。候选技能必须先通过确定性的离线评估才能被批准;
          只有已批准的技能会被注入后续回答。可在「任务」页签中对已完成任务执行「蒸馏技能」。
          点开「评估用例」可在评估前审查或修改用例。
        </p>
        {error && <p className="error">{error}</p>}
      </div>
      {renderSection("已通过", approved, {})}
      {renderSection("测试中(待审批)", testing, {
        evaluate: true,
        approve: true,
      })}
      {renderSection("候选", candidates, { evaluate: true })}
    </div>
  );
}
