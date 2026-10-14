export type ColType = "text" | "number" | "bool" | "date" | "datetime";
export type Operator = "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "contains" | "startswith";
export type Logic = "AND" | "OR";
export type CellValue = string | number | boolean | null;
export type ReportRow = Record<string, CellValue>;

/** One column of a master, as returned by GET /reports/masters/ */
export interface ReportColumnMeta {
  key: string;
  label: string;
  type: ColType;
  default: boolean;
  filterable: boolean;
  operators: Operator[];
  /** Non-empty for a child report's parent columns (e.g. "Party"): shown grouped/shaded. */
  group?: string;
}

export interface MasterMeta {
  key: string;
  label: string;
  columns: ReportColumnMeta[];
}

/** A filter row in the UI. Number values stay strings while typing. */
export interface Condition {
  id: string;
  column: string;
  op: Operator;
  value: string | boolean;
}

export interface ConditionPayload {
  column: string;
  op: Operator;
  value: string | number | boolean;
}

export interface ReportRequest {
  columns: string[];
  conditions: ConditionPayload[];
  logic: Logic;
  page?: number;
  page_size?: number;
  include_business_header?: boolean;
  include_serial_no?: boolean;
  /** Child reports: fill the parent's columns only on the first row of each parent. */
  parent_once?: boolean;
}

export interface BusinessHeader {
  name: string;
  address: string;
  contact: string;
}

export interface RunResponse {
  master: string;
  label: string;
  columns: { key: string; label: string; type: ColType; group?: string }[];
  business: BusinessHeader | null;
  count: number;
  page: number;
  page_size: number;
  /** Above this many rows the Excel export runs as a background job. */
  sync_export_limit: number;
  results: ReportRow[];
}

export interface ReportTemplateInput {
  master_key: string;
  name: string;
  columns: string[];
  conditions: ConditionPayload[];
  logic: Logic;
  show_business_header: boolean;
  show_serial_no: boolean;
  show_parent_once: boolean;
  is_shared: boolean;
}

export interface ReportTemplate extends ReportTemplateInput {
  id: number;
  owner: string;
  created_at: string;
  updated_at: string;
}

export type ExportJobStatus = "pending" | "running" | "done" | "failed";

export interface ExportJob {
  id: number;
  master_key: string;
  status: ExportJobStatus;
  row_count: number | null;
  file_name: string;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
