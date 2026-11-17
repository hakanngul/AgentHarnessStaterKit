import { useEffect, useState } from "react";
import { JobsPanel } from "./JobsPanel";
import { TaskList } from "./TaskList";
import type { Api } from "./lib/api";

type Tab = "tasks" | "jobs";

function tabFromHash(hash: string): Tab {
  return hash === "#/jobs" ? "jobs" : "tasks"; // hash routing: the server has no history fallback
}

export function App({ api }: { api: Api | null }) {
  const [tab, setTab] = useState<Tab>(() => tabFromHash(window.location.hash));

  useEffect(() => {
    const onChange = () => setTab(tabFromHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  if (api === null) {
    return (
      <main>
        <h1>Agent Harness</h1>
        <p role="alert">
          Token yok. Adresi <code>#token=&lt;token&gt;</code> ile açın (token, <code>harness serve</code>{" "}
          çıktısındaki <code>token:</code> satırıdır).
        </p>
      </main>
    );
  }

  return (
    <main>
      <h1>Agent Harness</h1>
      <nav aria-label="Bölümler">
        <a href="#/tasks" aria-current={tab === "tasks" ? "page" : undefined}>
          Görevler
        </a>{" "}
        <a href="#/jobs" aria-current={tab === "jobs" ? "page" : undefined}>
          İşler
        </a>
      </nav>
      {tab === "tasks" ? <TaskList api={api} /> : <JobsPanel api={api} />}
    </main>
  );
}
