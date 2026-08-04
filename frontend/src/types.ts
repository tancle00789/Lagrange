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

export interface TaskStatus {
  task_id: string;
  tenant_id: string;
  status: string;
  result: string | null;
  report: Report | null;
  knowledge_hits: KnowledgeHit[];
  external_sources: LiteratureRecord[];
  messages: { role: string; content: string }[];
}

export interface TaskEvent {
  type: string;
  [key: string]: unknown;
}
