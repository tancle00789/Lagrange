export interface SourceReference {
  kb_id: string;
  document_id: string;
  title: string;
  chunk_index: number;
}

export interface KnowledgeHit {
  chunk_id: string;
  score: number;
  source: SourceReference;
  snippet: string;
}

export interface Claim {
  text: string;
  chunk_ids: string[];
}

export interface LiteratureRecord {
  source_type: string;
  external_id: string;
  title: string;
  authors: string[];
  abstract: string;
  url: string;
  pdf_url: string | null;
  doi: string | null;
  venue: string;
  published: string;
  extra: Record<string, unknown>;
}

export function sourceId(record: LiteratureRecord): string {
  return `${record.source_type}:${record.external_id}`;
}

export interface Report {
  answer: string;
  claims: Claim[];
  grounded: boolean;
  dropped_citations: number;
  notes: string[];
}

export interface ModelRun {
  run_id: string;
  block_name: string;
  status: string;
  metrics: Record<string, number>;
  artifact_ids: string[];
  error: string | null;
}

export interface ModelBlockInfo {
  name: string;
  version: string;
  description: string;
  kind: string;
  gpu_required: boolean;
  max_runtime_seconds: number;
  params_schema: Record<string, unknown>;
}

export interface TaskStatus {
  task_id: string;
  tenant_id: string;
  status: string;
  result: string | null;
  report: Report | null;
  knowledge_hits: KnowledgeHit[];
  external_sources: LiteratureRecord[];
  model_runs: ModelRun[];
  messages: { role: string; content: string }[];
}

export interface TaskEvent {
  type: string;
  [key: string]: unknown;
}

export interface TriggerCondition {
  keywords: string[];
  regex_patterns: string[];
  priority: number;
}

export interface EvalCase {
  case_id: string;
  query: string;
  expected_answer_contains: string[];
  expect_skill_matched: string | null;
  expect_model_blocks: string[];
}

export interface EvalResult {
  run_id: string;
  total_cases: number;
  passed_cases: number;
  eval_pass_rate: number;
  details: {
    case_id: string;
    passed: boolean;
    failures: string[];
  }[];
  compared_to_version: string | null;
  regression_detected: boolean;
  judge_notes: string[];
}

export type SkillStatus = "candidate" | "testing" | "approved" | "deprecated";

export interface SkillRecord {
  skill_id: string;
  version: string;
  status: SkillStatus;
  name: string;
  description: string;
  trigger_conditions: TriggerCondition[];
  procedure: string;
  required_tools: string[];
  source_task_ids: string[];
  suggested_eval_cases: EvalCase[];
  eval_pass_rate: number;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
  parent_version: string | null;
}

export interface DistillResponse {
  worth_saving: boolean;
  record: SkillRecord | null;
  eval_cases: EvalCase[];
}

export interface EvaluateResponse {
  eval_result: EvalResult;
  record: SkillRecord;
}
