import { useState } from "react";
import { createTask } from "../api";
import type { TaskStatus } from "../types";

interface Props {
  onCreated: (task: TaskStatus) => void;
}

export default function TaskForm({ onCreated }: Props) {
  const [input, setInput] = useState("");
  const [requireApproval, setRequireApproval] = useState(false);
  const [allowExternal, setAllowExternal] = useState(false);
  const [ingestExternal, setIngestExternal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const task = await createTask({
        input,
        requireApproval,
        allowExternal,
        ingestExternal: allowExternal && ingestExternal,
      });
      setInput("");
      onCreated(task);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <h2>New Task</h2>
      <textarea
        value={input}
        placeholder="Ask a research question..."
        onChange={(e) => setInput(e.target.value)}
      />
      <label>
        <input
          type="checkbox"
          checked={requireApproval}
          onChange={(e) => setRequireApproval(e.target.checked)}
        />{" "}
        require approval before completion
      </label>
      <label>
        <input
          type="checkbox"
          checked={allowExternal}
          onChange={(e) => {
            setAllowExternal(e.target.checked);
            if (!e.target.checked) setIngestExternal(false);
          }}
        />{" "}
        allow external literature search (arxiv/github/crossref/nature)
      </label>
      <label>
        <input
          type="checkbox"
          checked={ingestExternal}
          disabled={!allowExternal}
          onChange={(e) => setIngestExternal(e.target.checked)}
        />{" "}
        ingest external PDFs into the knowledge base
      </label>
      <div className="actions">
        <button disabled={busy || !input.trim()} onClick={() => void submit()}>
          {busy ? "Creating..." : "Create task"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
