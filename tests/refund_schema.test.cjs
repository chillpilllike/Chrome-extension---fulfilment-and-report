const {test}=require('node:test');const assert=require('node:assert/strict')
const fs=require('node:fs');const Module=require('node:module')
const ts=require('../frontend/node_modules/typescript')
const mod=new Module('refundSchema')
mod._compile(ts.transpileModule(fs.readFileSync('frontend/src/components/refundSchema.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,'refundSchema.js')
const {schemaParams,reconcileSchema,changedSchemaValues}=mod.exports
const schemas=require('./fixtures/airwallex_bank_schemas.json')
const prefix='beneficiary.bank_details.'
test('PayID selection refreshes schema and removes BSB/account number',()=>{
 const previous={[prefix+'local_clearing_system']:'NPP',[prefix+'account_routing_type1']:'bsb',[prefix+'account_routing_value1']:'123456',[prefix+'account_number']:'123456789'}
 const changed=changedSchemaValues(previous,schemas['schema-NPP'],prefix+'account_routing_type1','email_address')
 assert.equal(schemaParams(changed,schemas['schema-NPP']).account_routing_type1,'email_address')
 assert.equal(changed[prefix+'account_number'],undefined)
 assert.equal(changed[prefix+'account_routing_value1'],undefined)
 const reconciled=reconcileSchema(changed,schemas['direct-AU-email_address'])
 assert.equal(reconciled[prefix+'account_number'],undefined)
 assert.equal(reconciled[prefix+'account_routing_type1'],'email_address')
})
test('switching country/method resets identifier, destination and clearing system',()=>{
 for(const path of [prefix+'bank_country_code','transfer_method']){
  const changed=changedSchemaValues({[prefix+'account_routing_type1']:'email_address',[prefix+'account_routing_value1']:'private@example.com',[prefix+'local_clearing_system']:'NPP'},schemas['direct-AU-email_address'],path,'SWIFT')
  assert.equal(changed[prefix+'local_clearing_system'],undefined)
  assert.equal(changed[prefix+'account_routing_type1'],undefined)
  assert.equal(changed[prefix+'account_routing_value1'],undefined)
 }
})
test('fixed routing defaults replace old values when returning to bank transfer',()=>{
 const next=reconcileSchema({[prefix+'account_routing_type1']:'email_address'},schemas['schema-BANK_TRANSFER'])
 assert.equal(next[prefix+'account_routing_type1'],'bsb')
})
test('all schema refresh fields are forwarded, including Interac identifiers',()=>{
 const params=schemaParams({[prefix+'account_routing_type1']:'phone_number','beneficiary.address.postcode':'A1A1A1'},schemas['direct-CA-INTERAC'])
 assert.equal(params.account_routing_type1,'phone_number')
})
test('business PayID choices and country method choices survive schema rendering',()=>{
 for(const [name,expected] of [['direct-AU-australian_business_number','organisation_identifier'],['direct-CA-INTERAC','phone_number']]){
  const schema=schemas[name], f=schema.fields.find(f=>f.path===prefix+'account_routing_type1')
  assert.ok(f.field.options.some(o=>o.value===expected))
 }
 for(const [name,expected] of [['direct-HK-FPS','RTGS'],['direct-SG-FAST','GIRO']]){
  const f=schemas[name].fields.find(f=>f.path===prefix+'local_clearing_system')
  assert.ok(f.field.options.some(o=>o.value===expected))
 }
})
