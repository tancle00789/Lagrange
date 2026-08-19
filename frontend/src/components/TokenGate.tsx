import { useState } from "react";

interface Props {
  onSave: (token: string) => void;
}

export default function TokenGate({ onSave }: Props) {
  const [value, setValue] = useState("");

  return (
    <div className="gate">
      <div className="panel">
        <h2>访问令牌</h2>
        <p className="muted">
          开发令牌格式：<code>dev:tenant-id:user-id:role</code>
        </p>
        <input
          type="text"
          value={value}
          placeholder="访问令牌"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && value.trim()) onSave(value.trim());
          }}
        />
        <div className="actions">
          <button disabled={!value.trim()} onClick={() => onSave(value.trim())}>
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
