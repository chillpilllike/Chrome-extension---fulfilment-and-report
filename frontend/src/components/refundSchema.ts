export type Option = {label:string;value:string;description?:string}
export type SchemaField = {path:string;enabled?:boolean;required:boolean;field:{key:string;label:string;type:string;options?:Option[];default?:string;description?:string;refresh?:boolean;example?:string;placeholder?:string}}
export type Schema = {fields:SchemaField[]}
export const selectors:Record<string,string> = {
 'beneficiary.bank_details.bank_country_code':'bank_country_code',
 'beneficiary.bank_details.account_currency':'account_currency',
 'beneficiary.entity_type':'entity_type', 'transfer_method':'transfer_method',
 'beneficiary.bank_details.local_clearing_system':'local_clearing_system',
 'beneficiary.address.country_code':'country_code', 'beneficiary.type':'type',
}
export const fieldPath=(f:SchemaField)=>f.path==='transfer_methods'?'transfer_method':f.path
export function schemaSelectors(schema:Schema|null):Record<string,string>{
 return {...selectors,...Object.fromEntries((schema?.fields||[]).filter(f=>f.field.refresh).map(f=>[fieldPath(f),f.field.key]))}
}
export function schemaParams(values:Record<string,string>,schema:Schema|null){
 return Object.fromEntries(Object.entries(schemaSelectors(schema)).filter(([p])=>values[p]).map(([p,k])=>[k,values[p]]))
}
export function reconcileSchema(values:Record<string,string>,schema:Schema){
 const next:Record<string,string>=Object.fromEntries(Object.entries(values).filter(([p])=>p.startsWith('beneficiary.address.')||p==='beneficiary.bank_details.account_name'))
 for(const f of schema.fields){
  const p=fieldPath(f),options=f.field.options
  let value=f.enabled===false?f.field.default:values[p]
  if(options?.length&&value&&!options.some(o=>o.value===value))value=''
  next[p]=value||f.field.default||''
 }
 return next
}
export function changedSchemaValues(values:Record<string,string>,schema:Schema|null,path:string,value:string){
 const keys=schemaSelectors(schema)
 let next={...values,[path]:value}
 if(['bank_country_code','transfer_method','entity_type','local_clearing_system'].includes(keys[path])){
  // Retain identity/address, but never carry a destination into a different route.
  next=Object.fromEntries(Object.entries(next).filter(([p])=>selectors[p]||p.startsWith('beneficiary.address.')||p==='beneficiary.bank_details.account_name'))
  if(['bank_country_code','transfer_method'].includes(keys[path]))delete next['beneficiary.bank_details.local_clearing_system']
  if(keys[path]==='bank_country_code')delete next.transfer_method
 }else if(/account_routing_type[12]$/.test(path)){
  delete next['beneficiary.bank_details.account_number']
  delete next['beneficiary.bank_details.iban']
  delete next['beneficiary.bank_details.account_routing_value1']
  delete next['beneficiary.bank_details.account_routing_value2']
  if(path.endsWith('type1'))delete next['beneficiary.bank_details.account_routing_type2']
 }else if(path==='beneficiary.address.country_code')delete next['beneficiary.address.state']
 return next
}
