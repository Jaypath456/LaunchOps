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
  id: string;
  job_title: string;
  department: string;
  start_date: string;
  date_needs_correction: boolean;
}

interface StepFields {
  key: string;
  title: string;
  description: string;
  owner_role: UserRole;
  kind: string;
  due_at: string;
  overdue: boolean;
  completed_by?: string | null;
  prerequisites: string[];
  completion_mode: string;
}

export type Step = StepFields & (
  | { state: 'done'; completed_at: string }
  | { state: 'ready' | 'blocked'; completed_at: null }
);

export interface Workflow extends WorkflowSummary {
  created_at: string;
  status: string;
  steps: Step[];
  events: WorkflowEvent[];
}

export interface WorkflowEvent {
  // The API uses the event's array index, rather than an ObjectId.
  id: number;
  actor: string | null;
  step: string | null;
  action: string;
  detail: string | null;
  created_at: string;
}

export interface TimelineResponse {
  id: string;
  events: WorkflowEvent[];
}

export interface Directory {
  users: { id: string; username: string; role: UserRole }[];
}

export interface WorkflowPage {
  count: number;
  page: number;
  total_pages: number;
  next: string | null;
  previous: string | null;
  results: Workflow[];
}

export interface WorkflowForm {
  employee_id: string;
  manager_id: string;
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
  reviewed_tasks?: string[];
}

export interface ProvisioningResult {
  connector: string;
  status: 'pending' | 'running' | 'retry' | 'succeeded' | 'dead' | 'overridden';
  external_ref: string | null;
  override_reason: string | null;
}

export interface ProvisioningJob extends ProvisioningResult {
  attempts: number;
  last_error: string | null;
  lease_owner: string | null;
  updated_at: string | null;
}

export type TaskStep = Step & {
  evidence: { [key: string]: unknown; _proof?: EvidenceProof; provisioning?: ProvisioningResult[] };
  form_fields: FormField[];
};

export interface TaskDetailResponse {
  step: TaskStep;
  workflow: WorkflowSummary;
  dependencies: { key: string; title: string; completed: boolean }[];
  review: TaskStep[];
  handbook: { version: string; sections: string[] } | null;
  issues: { id: string; detail: string; resolution: string; resolved_at: string | null }[];
  provisioning: ProvisioningJob[] | null;
}

export interface CompletionResponse {
  key: string;
  state: 'done';
  completion: {
    by: string | null;
    at: string;
    evidence: Record<string, unknown> | null;
    proof: EvidenceProof | null;
    request_id: string | null;
  };
}

export interface ResolutionResponse {
  changed: boolean;
}
