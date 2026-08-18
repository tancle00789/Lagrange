import type {
  DistillResponse,
  EvalCase,
  EvaluateResponse,
  LiteratureRecord,
  ModelBlockInfo,
  SkillRecord,
  SkillStatus,
  TaskStatus,
} from "./types";

const TOKEN_KEY = "pk_agent_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status}: ${body.slice(0, 200)}`);
  }
  return (await response.json()) as T;
}

export function listTasks(): Promise<TaskStatus[]> {
  return request<TaskStatus[]>("/api/v1/tasks");
}

export interface CreateTaskOptions {
  input: string;
  requireApproval: boolean;
  allowExternal: boolean;
  ingestExternal: boolean;
}

export function createTask(options: CreateTaskOptions): Promise<TaskStatus> {
  return request<TaskStatus>("/api/v1/tasks", {
    method: "POST",
    body: JSON.stringify({
      input: options.input,
      require_approval: options.requireApproval,
      allow_external: options.allowExternal,
      ingest_external: options.ingestExternal,
    }),
  });
}

export interface LiteratureSearchResult {
  records: LiteratureRecord[];
  errors: string[];
}

export function searchLiterature(
  query: string,
  sources?: string[],
  limit = 5,
): Promise<LiteratureSearchResult> {
  return request<LiteratureSearchResult>("/api/v1/literature/search", {
    method: "POST",
    body: JSON.stringify({ query, sources: sources ?? null, limit }),
  });
}

export function getTask(taskId: string): Promise<TaskStatus> {
  return request<TaskStatus>(`/api/v1/tasks/${taskId}`);
}

export function listModelBlocks(): Promise<ModelBlockInfo[]> {
  return request<ModelBlockInfo[]>("/api/v1/modelblocks");
}

export async function downloadArtifact(artifactId: string): Promise<void> {
  const response = await fetch(`/api/v1/artifacts/${artifactId}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!response.ok) {
    throw new Error(`${response.status}: artifact download failed`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = artifactId.split("/").pop() ?? "artifact";
  anchor.click();
  URL.revokeObjectURL(url);
}

export function resumeTask(taskId: string, approved: boolean, note?: string): Promise<TaskStatus> {
  return request<TaskStatus>(`/api/v1/tasks/${taskId}/resume`, {
    method: "POST",
    body: JSON.stringify({ approved, note: note ?? null }),
  });
}

export function cancelTask(taskId: string): Promise<TaskStatus> {
  return request<TaskStatus>(`/api/v1/tasks/${taskId}`, { method: "DELETE" });
}

export function listApprovedSkills(): Promise<SkillRecord[]> {
  return request<SkillRecord[]>("/api/v1/skills");
}

export function listSkillPipeline(status: SkillStatus): Promise<SkillRecord[]> {
  return request<SkillRecord[]>(`/api/v1/skills/pipeline?status=${status}`);
}

export function distillSkill(taskId: string): Promise<DistillResponse> {
  return request<DistillResponse>("/api/v1/skills/distill", {
    method: "POST",
    body: JSON.stringify({ task_id: taskId }),
  });
}

export function evaluateSkill(
  skillId: string,
  version: string,
  cases?: EvalCase[],
): Promise<EvaluateResponse> {
  // Without explicit cases the backend falls back to the record's
  // distill-suggested cases.
  return request<EvaluateResponse>(`/api/v1/skills/${skillId}/${version}/evaluate`, {
    method: "POST",
    body: JSON.stringify({ cases: cases ?? [] }),
  });
}

export function approveSkill(skillId: string, version: string): Promise<SkillRecord> {
  return request<SkillRecord>(`/api/v1/skills/${skillId}/${version}/approve`, {
    method: "POST",
  });
}

export function deprecateSkill(skillId: string, version: string): Promise<SkillRecord> {
  return request<SkillRecord>(`/api/v1/skills/${skillId}/${version}/deprecate`, {
    method: "POST",
  });
}

export function subscribeTaskEvents(
  taskId: string,
  onEvent: (event: string, data: string) => void,
): () => void {
  let source: EventSource | null = null;
  let closed = false;
  void request<{ ticket: string; expires_in: number }>(
    `/api/v1/tasks/${taskId}/events/ticket`,
    { method: "POST" },
  )
    .then(({ ticket }) => {
      if (closed) return;
      source = new EventSource(
        `/api/v1/tasks/${taskId}/events?ticket=${encodeURIComponent(ticket)}`,
      );
      const handler = (event: MessageEvent) => onEvent(event.type, event.data);
      source.onmessage = handler;
      for (const type of [
        "snapshot",
        "started",
        "skills_matched",
        "knowledge_retrieved",
        "knowledge_degraded",
        "external_retrieved",
        "external_degraded",
        "external_ingested",
        "model_called",
        "model_calls_planned",
        "model_run_completed",
        "model_run_failed",
        "citation_gate",
        "paused",
        "completed",
        "failed",
        "cancelled",
      ]) {
        source.addEventListener(type, handler);
      }
    })
    .catch((error: unknown) => {
      if (!closed) onEvent("failed", error instanceof Error ? error.message : String(error));
    });
  return () => {
    closed = true;
    source?.close();
  };
}
