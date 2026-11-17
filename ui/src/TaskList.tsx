import { useCallback, useEffect, useState } from "react";
import type { Api } from "./lib/api";
import type { TaskRow } from "./lib/types";

export function TaskList({ api }: { api: Api }) {
  const [rows, setRows] = useState<TaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api
      .get<TaskRow[]>("/api/tasks")
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }, [api]);

  useEffect(load, [load]);

  return (
    <section aria-labelledby="tasks-title">
      <h2 id="tasks-title">Görevler</h2>
      <button type="button" onClick={load}>
        Yenile
      </button>
      {error && <p role="alert">{error}</p>}
      {rows === null && !error && <p>Yükleniyor…</p>}
      {rows !== null && rows.length === 0 && <p>Görev yok.</p>}
      {rows !== null && rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Id</th>
              <th>Başlık</th>
              <th>Durum</th>
              <th>Kabul</th>
              <th>Review</th>
              <th>Kapsam</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) =>
              row.error ? (
                <tr key={row.id}>
                  <td>{row.id}</td>
                  <td colSpan={5}>Okunamadı: {row.error}</td>
                </tr>
              ) : (
                <tr key={row.id}>
                  <td>{row.id}</td>
                  <td>{row.title}</td>
                  <td>
                    <span className={`status status-${row.status ?? "unknown"}`}>{row.status}</span>
                  </td>
                  <td>
                    {row.verified ?? 0}/{row.acceptance ?? 0}
                  </td>
                  <td>{row.review ?? "—"}</td>
                  <td>{(row.scope ?? []).join(", ")}</td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      )}
    </section>
  );
}
