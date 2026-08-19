import { useCallback, useEffect, useState } from "react";
import { getToken, listTasks, setToken } from "./api";
import type { TaskStatus } from "./types";
import TokenGate from "./components/TokenGate";
import TaskForm from "./components/TaskForm";
import TaskList from "./components/TaskList";
import TaskDetail from "./components/TaskDetail";
import SkillsPanel from "./components/SkillsPanel";

export default function App() {
  const [authed, setAuthed] = useState<boolean>(getToken() !== "");
  const [view, setView] = useState<"tasks" | "skills">("tasks");
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
    if (!authed || view !== "tasks") return;
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [authed, view, refresh]);

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
        <nav className="tabs">
          <button
            className={view === "tasks" ? "link active" : "link"}
            onClick={() => setView("tasks")}
          >
            任务
          </button>
          <button
            className={view === "skills" ? "link active" : "link"}
            onClick={() => setView("skills")}
          >
            技能
          </button>
        </nav>
        <button
          className="link"
          onClick={() => {
            setToken("");
            setAuthed(false);
          }}
        >
          更换令牌
        </button>
      </header>
      {view === "skills" ? (
        <main>
          <section className="detail">
            <SkillsPanel />
          </section>
        </main>
      ) : (
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
              <p className="muted">选择一个任务，或新建一个。</p>
            )}
          </section>
        </main>
      )}
    </div>
  );
}
