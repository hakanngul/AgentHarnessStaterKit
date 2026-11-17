export interface TaskRow {
  id: string;
  title?: string;
  owner?: string;
  status?: string;
  scope?: string[];
  acceptance?: number;
  verified?: number;
  review?: string | null;
  error?: string;
}

export type JobKind = "wave" | "review" | "commit";
export type JobStatus = "running" | "done" | "failed" | "cancelled";

export interface JobView {
  id: string;
  kind: JobKind;
  status: JobStatus;
  params: Record<string, unknown>;
  created: string;
  finished: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
  last_event: number;
}

export interface JobEvent {
  id: number;
  task: string | null;
  text: string;
}
