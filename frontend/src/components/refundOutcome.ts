export const statusLabel = (status:string) => ({PAID:'Completed — paid by Airwallex',PROCESSING:'Awaiting Airwallex completion',SCHEDULED:'Scheduled — not completed',SUBMITTING:'Sending request — not confirmed',UNKNOWN:'Not confirmed — needs attention',REJECTED:'Rejected — not sent',FAILED:'Failed',CANCELLED:'Cancelled',CANCELED:'Cancelled',PENDING_APPROVAL:'Awaiting approval — not completed',PENDING:'Pending — not completed'}[status]||'Status needs review')
export function refundOutcome(result:{order_name:string;status:string;last_error?:string}){
 const error=['UNKNOWN','REJECTED','FAILED','CANCELLED','CANCELED'].includes(result.status)||!['PAID','PROCESSING','SCHEDULED','SUBMITTING','PENDING_APPROVAL','PENDING'].includes(result.status)
 const detail=result.status==='PAID'?'Airwallex confirmed this refund as paid.':error?'The refund is not confirmed as completed. Check its status before sending another refund.':'The refund is not completed yet. We are waiting for Airwallex’s final status. Do not send another refund.'
 return {error,message:`${result.order_name}: ${statusLabel(result.status)}. ${result.last_error||detail}`}
}
