export type WorkPlanType = "work" | "temporary_unavailable";
export type WorkPlanStatus = "active" | "cancelled";
export type CollaborationStatus = "online" | "offline" | "planned_offline" | "temporary_unavailable";
export type WorkPlanRange = "7d" | "30d" | "all";

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
};

export type WorkPlanScheduleResponse = {
  members: WorkPlanMember[];
  plans: WorkPlan[];
  start_date: string;
  end_date: string;
  observed_at: string;
  timezone: string;
};

export type WorkPlanHistoryResponse = {
  items: WorkPlan[];
  total: number;
};

export type WorkPlanCreatePayload = {
  plan_type: WorkPlanType;
  dates: string[];
  start_time: string;
  end_time: string;
  note: string | null;
  idempotency_key: string;
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
    outcome: "created" | "duplicate" | "failed";
    plan?: WorkPlan;
    error?: string;
  }>;
};
