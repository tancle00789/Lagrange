import { useCallback, useEffect, useState } from "react";
import { getToken, listTasks, setToken } from "./api";
import type { TaskStatus } from "./types";
import TokenGate from "./components/TokenGate";
import TaskForm from "./components/TaskForm";
import TaskList from "./components/TaskList";
import TaskDetail from "./components/TaskDetail";

export default function App() {
  const [authed, setAuthed] = useState<boolean>(getToken() !== "");
  const [tasks, setTasks] = useState<TaskStatus[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      const items = await listTasks();
      setTasks(items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (!authed) return;
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [authed, refresh]);

  if (!authed) {
    return (
      <TokenGate
        onSave={(token) => {
          setToken(token);
          setAuthed(true);
        }}
      />
    );
  }

  const selected = tasks.find((t) => t.task_id === selectedId) ?? null;

  return (
    <div className="layout">
      <header>
        <h1>Production Knowledge Agent</h1>
        <button
          className="link"
          onClick={() => {
            setToken("");
            setAuthed(false);
          }}
        >
          change token
        </button>
      </header>
      <main>
        <section className="sidebar">
          <TaskForm
            onCreated={(task) => {
              setSelectedId(task.task_id);
              void refresh();
            }}
          />
          <TaskList tasks={tasks} selectedId={selectedId} onSelect={setSelectedId} />
          {error && <p className="error">{error}</p>}
        </section>
        <section className="detail">
          {selected ? (
            <TaskDetail key={selected.task_id} task={selected} onChanged={refresh} />
          ) : (
            <p className="muted">Select a task or create a new one.</p>
          )}
        </section>
      </main>
    </div>
  );
}
