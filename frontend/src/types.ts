export type ViewName =
  | "upload"
  | "todos"
  | "push-error-todos"
  | "accounts"
  | "available-pool"
  | "reserve-pool"
  | "api-pools"
  | "event-records"
  | "alert-center"
  | "pool-lifecycle"
  | "agent-analysis"
  | "api-tokens"
  | "users"
  | "logs";

export type UserRole = "owner" | "admin" | "maintainer" | "viewer";
export type UserStatus = "active" | "disabled" | "pending_password_reset";

export type User = {
  id?: string;
  email: string;
  name?: string;
  role: UserRole;
  status?: UserStatus;
};

export type AccountDocument = {
  id: string;
  account_json: Record<string, unknown>;
  metadata: Record<string, unknown>;
};

export type PoolStatus = "library" | "available" | "reserve" | "active" | "problem" | "discarded";

export type UploadMode = "fill" | "parse";
export type UploadTemplate = "sub2api" | "purchased_jinyao";
export type AccountType = "plus" | "team" | "k12" | "free" | "pro" | "other";

export type UploadFields = {
  email_session: string;
  account_type: AccountType;
  payment_type: "paypal_multi" | "paypal_single" | "no_card" | "gopay" | "other";
  twoFA: string;
  self_produced: "true" | "false";
  purchase_source: string;
  purchase_account_type: AccountType | "";
  phone_bound: "true" | "false";
  phone_number: string;
  remark: string;
  manual_status_label: string;
  account_json: string;
};

export type ApiPool = {
  id: string;
  name: string;
  account_type: AccountType;
  site_id: string;
  active_group_id: number;
  verification_group_id?: number | null;
  min_active: number;
  target_active: number;
  max_avg_5h_used: number;
  max_avg_7d_used: number;
  min_reserve: number;
  status: "active" | "disabled";
  created_at?: string;
  updated_at?: string;
};

export type TodoItem = {
  id: string;
  dedupe_key?: string;
  todo_type: string;
  pool_id?: string | null;
  title: string;
  summary?: Record<string, unknown>;
  suggested_action?: string | null;
  status: string;
  occurrence_count?: number;
  created_at?: string;
  updated_at?: string;
};

export type ImportBatchResult = {
  batch: Record<string, unknown>;
  created: string[];
  updated: string[];
  blocked?: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
};
