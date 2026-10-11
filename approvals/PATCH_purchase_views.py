# PATCH to purchase/views.py - wires PurchaseRequisitionViewSet up to the
# generic approval workflow. Two changes to the file delivered earlier:

# 1. Add this import:

# 2. Change the class declaration from:
#      class PurchaseRequisitionViewSet(viewsets.ModelViewSet):
#    to:
#      class PurchaseRequisitionViewSet(ApprovalActionMixin, viewsets.ModelViewSet):

# That's it - this gives PR, for free:
#   POST /purchase-requisitions/{id}/decision/          {"decision": "Approved", "reason": "..."}
#   POST /purchase-requisitions/bulk-decision/           {"ids": [1,2,3], "decision": "Approved"}
#   GET  /purchase-requisitions/{id}/approval-history/
#
# perform_update/perform_destroy (already in that file) still separately
# block modifying/deleting a non-Pending PR - the decision endpoint is the
# ONLY way status changes now, matching "PR Approval screen" in the
# architecture doc.
