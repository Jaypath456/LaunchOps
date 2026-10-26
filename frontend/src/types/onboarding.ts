import type { UserRole } from './auth';

export interface TaskOwner {
  role: string;
  username?: string;
}

export interface WorkflowOwners {
  employee: string;
  manager: string;
}

export interface TaskState {
  owner_role: string;
  state: string;
}

export interface WorkflowSummary extends WorkflowOwners {
  id: number;
  job_title: string;
  department: string;
  start_date: string;
  training_course_id: number | null;
  date_needs_correction: boolean;
}

interface StepFields {
  id: number;
  key: string;
  title: string;
  description: string;
  owner_role: UserRole;
  kind: string;
  due_at: string;
  completed_by?: string | null;
  prerequisites: string[];
  completion_mode: string;
}

export type Step = StepFields & (
  | { state: 'completed'; completed_at: string }
  | { state: 'ready' | 'blocked'; completed_at: null }
);

export interface Workflow extends WorkflowSummary {
  created_at: string;
  steps: Step[];
  events: {
    id: number;
    actor?: string | null;
    step?: string | null;
    action: string;
    detail: string;
    created_at: string;
  }[];
}

export interface Directory {
  users: { id: number; username: string; role: UserRole }[];
  tracks: { id: number; title: string }[];
}

export interface WorkflowForm {
  employee_id: string;
  manager_id: string;
  training_course_id: string;
  job_title: string;
  department: string;
  start_date: string;
}

export type FormField = { name: string; label: string } & (
  | { type: 'select'; options: string[] }
  | { type: 'checkbox' | 'date' | 'text' }
);

export interface EvidenceProof {
  mode: string;
  result: string;
  reference?: string;
  asset?: string;
  version?: string;
  profile?: Pick<WorkflowSummary, 'employee' | 'job_title' | 'department' | 'start_date'>;
  assessments?: { score: number; max_score: number }[];
  reviewed_tasks?: unknown[];
}

export type TaskStep = Step & {
  evidence: { [key: string]: unknown; _proof?: EvidenceProof };
  form_fields: FormField[];
};

export interface TaskDetailResponse {
  step: TaskStep;
  workflow: WorkflowSummary;
  dependencies: { id: number; title: string; completed: boolean }[];
  review: TaskStep[];
  handbook: { version: string; sections: string[] } | null;
  issues: { id: number; detail: string; resolution: string; resolved_at: string | null }[];
  lessons: { id: number; title: string; reviewed: boolean }[];
  assessments: { id: number; title: string; score: number | null; max_score: number | null; passed: boolean }[];
}

export interface CompletionResponse {
  id: number;
  changed: boolean;
  state: 'completed';
}

export interface ResolutionResponse {
  changed: boolean;
}
