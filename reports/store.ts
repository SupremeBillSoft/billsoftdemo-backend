import { create } from "zustand";

import type { ColType, Condition, Logic, MasterMeta, ReportColumnMeta, ReportTemplate } from "./types";

let seq = 0;
const nextId = () => `cond-${(seq += 1)}`;

export function defaultValue(type: ColType): Condition["value"] {
  return type === "bool" ? true : "";
}

interface MasterReportState {
  masterKey: string | null;
  templateId: number | null; // the saved template currently loaded, if any
  columns: string[]; // selected column keys, in display order
  conditions: Condition[];
  logic: Logic;
  showBusinessHeader: boolean;
  showSerialNo: boolean;
  parentOnce: boolean; // child reports: show parent columns once per parent
  page: number;
  pageSize: number;

  selectMaster: (master: MasterMeta | null) => void;
  applyTemplate: (master: MasterMeta, template: ReportTemplate) => void;
  setTemplateId: (id: number | null) => void;
  toggleColumn: (key: string) => void;
  setColumns: (keys: string[]) => void;
  moveColumn: (key: string, direction: -1 | 1) => void;
  addCondition: (master: MasterMeta) => void;
  updateCondition: (id: string, patch: Partial<Omit<Condition, "id">>) => void;
  changeConditionColumn: (id: string, col: ReportColumnMeta) => void;
  removeCondition: (id: string) => void;
  setLogic: (logic: Logic) => void;
  setShowBusinessHeader: (value: boolean) => void;
  setShowSerialNo: (value: boolean) => void;
  setParentOnce: (value: boolean) => void;
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
}

export const useMasterReportStore = create<MasterReportState>()((set) => ({
  masterKey: null,
  templateId: null,
  columns: [],
  conditions: [],
  logic: "AND",
  showBusinessHeader: true,
  showSerialNo: true,
  parentOnce: true,
  page: 1,
  pageSize: 50,

  selectMaster: (master) =>
    set({
      masterKey: master?.key ?? null,
      templateId: null,
      columns: master ? master.columns.filter((c) => c.default).map((c) => c.key) : [],
      conditions: [],
      logic: "AND",
      parentOnce: true,
      page: 1,
    }),

  // Columns or filters that no longer exist in the master are dropped, so an old template never breaks the screen.
  applyTemplate: (master, template) =>
    set({
      templateId: template.id,
      columns: template.columns.filter((k) => master.columns.some((c) => c.key === k)),
      conditions: template.conditions.flatMap((c) => {
        const col = master.columns.find((x) => x.key === c.column);
        if (!col || !col.filterable || !col.operators.includes(c.op)) return [];
        const value = typeof c.value === "boolean" ? c.value : String(c.value);
        return [{ id: nextId(), column: c.column, op: c.op, value }];
      }),
      logic: template.logic,
      showBusinessHeader: template.show_business_header,
      showSerialNo: template.show_serial_no,
      parentOnce: template.show_parent_once ?? true, // templates saved before child reports existed
      page: 1,
    }),

  setTemplateId: (id) => set({ templateId: id }),

  toggleColumn: (key) =>
    set((s) => ({
      columns: s.columns.includes(key) ? s.columns.filter((k) => k !== key) : [...s.columns, key],
    })),

  setColumns: (keys) => set({ columns: keys }),

  moveColumn: (key, direction) =>
    set((s) => {
      const i = s.columns.indexOf(key);
      const j = i + direction;
      if (i < 0 || j < 0 || j >= s.columns.length) return s;
      const next = [...s.columns];
      [next[i], next[j]] = [next[j], next[i]];
      return { columns: next };
    }),

  addCondition: (master) =>
    set((s) => {
      const col = master.columns.find((c) => c.filterable);
      if (!col) return s;
      const cond: Condition = { id: nextId(), column: col.key, op: col.operators[0], value: defaultValue(col.type) };
      return { conditions: [...s.conditions, cond], page: 1 };
    }),

  updateCondition: (id, patch) =>
    set((s) => ({
      conditions: s.conditions.map((c) => (c.id === id ? { ...c, ...patch } : c)),
      page: 1,
    })),

  changeConditionColumn: (id, col) =>
    set((s) => ({
      conditions: s.conditions.map((c) =>
        c.id === id ? { ...c, column: col.key, op: col.operators[0], value: defaultValue(col.type) } : c,
      ),
      page: 1,
    })),

  removeCondition: (id) => set((s) => ({ conditions: s.conditions.filter((c) => c.id !== id), page: 1 })),

  setLogic: (logic) => set({ logic, page: 1 }),
  setShowBusinessHeader: (value) => set({ showBusinessHeader: value }),
  setShowSerialNo: (value) => set({ showSerialNo: value }),
  setParentOnce: (value) => set({ parentOnce: value }),
  setPage: (page) => set({ page }),
  setPageSize: (pageSize) => set({ pageSize, page: 1 }),
}));
