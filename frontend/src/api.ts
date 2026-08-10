import type { LiteratureRecord, TaskStatus } from "./types";

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

export function resumeTask(taskId: string, approved: boolean, note?: string): Promise<TaskStatus> {
  return request<TaskStatus>(`/api/v1/tasks/${taskId}/resume`, {
    method: "POST",
    body: JSON.stringify({ approved, note: note ?? null }),
  });
}

export function cancelTask(taskId: string): Promise<TaskStatus> {
  return request<TaskStatus>(`/api/v1/tasks/${taskId}`, { method: "DELETE" });
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
        "knowledge_retrieved",
        "knowledge_degraded",
        "external_retrieved",
        "external_degraded",
        "external_ingested",
        "model_called",
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
