"""DOM regressions for Amazon recommendations embedded within purchased rows.
Run with NODE_PATH pointing to a Node installation of linkedom.
"""
from pathlib import Path
import subprocess
import unittest
CONTENT=(Path(__file__).resolve().parents[1]/'chrome-extension/content.js').read_text()
class CartRowOwnershipTests(unittest.TestCase):
 def test_real_dom_cart_ownership(self):
  available=subprocess.run(['node','-e','require("linkedom")'],capture_output=True)
  if available.returncode:self.skipTest('Install linkedom and set NODE_PATH for DOM tests')
  functions=CONTENT[CONTENT.index('function cartActiveRoots()'):CONTENT.index('function cartIsVisiblyEmpty()')]
  asin=CONTENT[CONTENT.index('function cartItemAsin('):CONTENT.index('function cartDiagnosticSummary(')]
  qty=CONTENT[CONTENT.index('function cartItemQuantity('):CONTENT.index('function cartActiveRoots(')]
  script=r'''
const assert=require('node:assert/strict');const {parseHTML}=require('linkedom');let document;
const visible=n=>n && !n.hasAttribute('hidden');
function row(asin,inner='',type='active') {return `<div class="sc-list-item" data-itemtype="${type}" data-asin="${asin}" data-quantity="3"><span>Pack of 2</span><button aria-label="Delete item">Delete</button>${inner}</div>`;}
function load(html){document=parseHTML(`<html><body><div id="sc-active-cart" data-csa-c-content-id="activeCart"><form id="activeCartViewForm"><div data-name="Active Items">${html}</div></form></div></body></html>`).document;}
'''+functions+asin+qty+r'''
const recommendation='<section><h2>Buy it again</h2><div data-asin="B012345678"><a href="/dp/B012345678">Recommendation</a><button>Add to Cart</button><span data-quantity="1"></span></div></section>';
load(row('B0BV67RXQH',recommendation));
assert.equal(cartActiveRoots().length,1,'duplicate root matches must not erase the root');
assert.equal(cartActiveItems().length,1);assert.equal(cartItemAsin(cartActiveItems()[0]),'B0BV67RXQH');assert.equal(cartItemQuantity(cartActiveItems()[0]),3);
load(row('B0BV67RXQH',recommendation)+row('B076F324JN'));
assert.deepEqual(cartActiveItems().map(cartItemAsin),['B0BV67RXQH','B076F324JN'],'a real second item must remain visible');
load(row('B0BV67RXQH')+row('B012345678','Saved for later','saved'));
assert.deepEqual(cartActiveItems().map(cartItemAsin),['B0BV67RXQH']);
load('<div class="sc-list-item" data-itemtype="active" data-asin="B0BV67RXQH">Item was removed</div>');
assert.equal(cartActiveItems().length,0,'removed notices are not active purchases');
load(row('B0BV67RXQH','Sponsored recommendation: '+recommendation));
assert.equal(cartActiveItems().length,1);
'''
  r=subprocess.run(['node','-e',script],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
 def test_single_item_smart_wagon_opens_full_cart_first(self):
  source=CONTENT[CONTENT.index('async function handleCart('):CONTENT.index('async function handleCart(')+1900]
  branch=source[source.index("if (!['clear_cart'"):source.index('if (shouldVerifyCurrentItemBeforeContinuing)')]
  self.assertIn('smart-wagon',branch);self.assertIn('location.href = "https://www.amazon.com/cart?ref_=sw_gtc"',branch)
  self.assertNotIn('nextIndex <',branch)
 def test_submitted_jobs_cannot_enter_failure_cleanup(self):
  bg=(Path(__file__).resolve().parents[1]/'chrome-extension/background.js').read_text()
  parts=[bg[bg.index('function jobWasSubmittedToAmazon('):bg.index('function activeJobBlocksNext(')],bg[bg.index('function orderSubmitStarted('):bg.index('function activeJobHasReportedOrderId(')],bg[bg.index('async function failJob('):bg.index('async function postSubmitUnplaced(')]]
  script='''const assert=require('node:assert/strict');let activeJob,recovered=0;
const forceStopActive=async()=>false;const getWindowState=async()=>({activeJob});const log=async()=>{};
const recoverSubmittedJobInWindow=async()=>{recovered++};
const heartbeatJob=async()=>{throw Error('Submitted job must not reach terminal failure')};
'''+''.join(parts)+'''
(async()=>{for(const marker of [{amazonSubmittedAt:1},{stage:'complete_pending'},{stage:'find_order_id'},{stage:'reporting_complete'},{pausedStage:'find_order_id'},{job:{group_key:'test',submitted_to_amazon:true}}]){
 activeJob={job:{group_key:'test'},...marker};const result=await failJob('late error',{},1);assert.equal(result.submitted_pending,true);
}assert.equal(recovered,6)})().catch(e=>{console.error(e);process.exit(1)});
'''
  r=subprocess.run(['node','-e',script],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
