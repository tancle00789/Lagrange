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
