// src/lib/approvals/useApprovalDecision.ts
//
// One generic hook factory, reused by PR and PO (and any future approvable
// document) instead of writing "useDecidePurchaseRequisition" /
// "useDecidePurchaseOrder" as separate copies of the same mutation.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../apiClient"; // adjust to this project's actual wrapper

export type ApprovalDecision = "Approved" | "Rejected" | "Hold";

export function useApprovalDecisionMutation(resourceBasePath: string, invalidateKey: readonly unknown[]) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, decision, reason }: { id: number; decision: ApprovalDecision; reason?: string }) => {
      const { data } = await apiClient.post(`${resourceBasePath}/${id}/decision/`, { decision, reason });
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invalidateKey }),
  });
}

export function useBulkApprovalDecisionMutation(resourceBasePath: string, invalidateKey: readonly unknown[]) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ ids, decision, reason }: { ids: number[]; decision: ApprovalDecision; reason?: string }) => {
      const { data } = await apiClient.post(`${resourceBasePath}/bulk-decision/`, { ids, decision, reason });
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invalidateKey }),
  });
}
