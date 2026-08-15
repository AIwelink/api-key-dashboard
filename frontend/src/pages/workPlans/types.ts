export type WorkPlanType = "work" | "temporary_unavailable";
export type WorkPlanStatus = "active" | "cancelled";
export type CollaborationStatus = "in_plan" | "online" | "offline" | "planned_offline" | "temporary_unavailable";
export type WorkPlanRange = "7d" | "30d" | "all";
export type WorkPlanOperationType = "activate" | "cancel";
export type WorkPlanSegmentState = "active" | "cancelled";

export type WorkPlan = {
  id: string;
  member_id: string;
  member_name: string;
  plan_type: WorkPlanType;
  plan_date: string;
  start_minute: number;
  end_minute: number;
  note: string | null;
  status: WorkPlanStatus;
  is_cancelled: boolean;
  created_at: string;
  updated_at: string;
  cancelled_at?: string | null;
  cancelled_by?: string | null;
  updated_by?: string | null;
};

export type WorkPlanMember = {
  member_id: string;
  member_name: string;
  member_email?: string | null;
  role?: string | null;
  account_status?: string | null;
  is_online: boolean;
  active_clients: number;
  last_seen_at?: string | null;
  active_plan?: WorkPlan | null;
  collaboration_status: CollaborationStatus;
  work_plan_priority?: number | null;
  current_green?: boolean;
  next_green_start?: string | null;
  latest_green_end?: string | null;
};

export type WorkPlanOperation = {
  id: string;
  schema_version: 2;
  record_kind: "operation";
  member_id: string;
  member_name: string;
  operation_type: WorkPlanOperationType;
  anchor_date: string;
  plan_date: string;
  requested_start_at: string;
  requested_end_at: string;
  effective_start_at: string;
  effective_end_at: string;
  start_offset_minute: number;
  end_offset_minute: number;
  requested_start_offset_minute: number;
  requested_end_offset_minute: number;
  effective_start_offset_minute: number;
  effective_end_offset_minute: number;
  member_sequence: number;
  idempotency_key: string;
  batch_id: string;
  note: string | null;
  compensates_operation_id?: string | null;
  compensation_group_id?: string | null;
  created_by: string;
  created_at: string;
  is_clipped?: boolean;
  history_state?: "active" | "cancelled" | "replaced";
  legacy_derived?: false;
};

export type WorkPlanSegment = {
  member_id: string;
  member_name: string;
  state: WorkPlanSegmentState;
  start_at: string;
  end_at: string;
  winning_operation_id: string;
  operation_ids: string[];
};

export type WorkPlanHistoryItem = WorkPlan | WorkPlanOperation;

export type WorkPlanScheduleResponse = {
  members: WorkPlanMember[];
  plans: WorkPlan[];
  segments?: WorkPlanSegment[];
  start_date: string;
  end_date: string;
  start_at?: string;
  end_at?: string;
  observed_at: string;
  timezone: string;
  total: number;
  has_more: boolean;
  next_cursor: string | null;
  total_operations?: number;
};

export type WorkPlanHistoryResponse = {
  items: WorkPlanHistoryItem[];
  total: number;
  has_more: boolean;
  next_cursor: string | null;
};

export type WorkPlanCreatePayload = {
  plan_type: WorkPlanType;
  dates: string[];
  start_time: string;
  end_time: string;
  note: string | null;
  idempotency_key: string;
};

export type WorkPlanOperationCreatePayload = {
  operation_type: WorkPlanOperationType;
  anchor_dates: string[];
  start_offset_minute: number;
  end_offset_minute: number;
  note: string | null;
  idempotency_key: string;
};

export type WorkPlanOperationUpdatePayload = {
  operation_type: WorkPlanOperationType;
  anchor_date: string;
  start_offset_minute: number;
  end_offset_minute: number;
  note: string | null;
  idempotency_key: string;
  expected_member_sequence: number;
};

export type WorkPlanPriorityUpdatePayload = {
  priority: number | null;
};

export type WorkPlanUpdatePayload = {
  plan_type?: WorkPlanType;
  start_time?: string;
  end_time?: string;
  note?: string | null;
  expected_updated_at?: string;
};

export type WorkPlanMutationResult = {
  duplicate_submission: boolean;
  total: number;
  results: Array<{
    plan_date: string;
    outcome: "created" | "duplicate" | "failed" | "uncertain";
    plan?: WorkPlan;
    operation?: WorkPlanOperation;
    operations?: WorkPlanOperation[];
    error?: string;
  }>;
};

export type WorkPlanPriorityResult = {
  member_id: string;
  member_name: string;
  role?: string | null;
  work_plan_priority: number | null;
};
