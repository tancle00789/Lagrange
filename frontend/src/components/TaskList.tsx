import type { TaskStatus } from "../types";

interface Props {
  tasks: TaskStatus[];
  selectedId: string | null;
  onSelect: (taskId: string) => void;
}

export default function TaskList({ tasks, selectedId, onSelect }: Props) {
  return (
    <div className="panel">
      <h2>任务</h2>
      {tasks.length === 0 && <p className="muted">暂无任务。</p>}
      {tasks.map((task) => (
        <div
          key={task.task_id}
          className={`task-item${task.task_id === selectedId ? " selected" : ""}`}
          onClick={() => onSelect(task.task_id)}
        >
          <span className={`status ${task.status}`}>{task.status}</span>
          <div className="id">{task.task_id}</div>
          <div>{task.messages[0]?.content.slice(0, 80) ?? ""}</div>
        </div>
      ))}
    </div>
  );
}
