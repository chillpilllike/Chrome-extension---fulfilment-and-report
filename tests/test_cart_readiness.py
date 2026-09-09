from pathlib import Path
import subprocess
import unittest
CONTENT=(Path(__file__).resolve().parents[1]/'chrome-extension/content.js').read_text()
class CartReadinessTests(unittest.TestCase):
 def test_delayed_unreadable_and_real_mismatch(self):
  helper=CONTENT[CONTENT.index('async function waitForReadableCart('):CONTENT.index('async function handleCart(')]
  wait=CONTENT[CONTENT.index('async function waitUntil('):CONTENT.index('function findUseAddressButton(')]
  script=r'''
const assert=require('node:assert/strict');let now=0,readyAt=0,result,saved=0;
Date.now=()=>now;const sleep=async ms=>{now+=ms};const waitIfPaused=async()=>{};
const verifyCartQuantities=()=>now<readyAt?{ok:false,unreadable_cart:true}:result;
const showPanel=()=>{};const setActiveJob=async()=>{saved++};
'''+wait+helper+r'''
(async()=>{
 result={ok:true,exact:true,quantities:{B0BV67RXQH:3}};
 let job={stage:'cart',paused:false};readyAt=6000;
 assert.equal(await waitForReadableCart(job),result);assert.equal(job.paused,false);assert.equal(saved,0);
 now=0;readyAt=Infinity;job={stage:'cart',paused:false};
 assert.equal(await waitForReadableCart(job),null);assert.equal(job.paused,true);assert.equal(job.pausedStage,'cart');assert.equal(saved,1);assert.equal(now,15000);
 now=0;readyAt=0;result={ok:false,identity_mismatch:true,unexpected_asins:['B076F324JN']};job={stage:'cart',paused:false};
 assert.equal(await waitForReadableCart(job),result);assert.equal(now,0);assert.equal(job.paused,false);
 now=0;readyAt=2000;assert.equal(await waitForReadableCart(job),result);assert.equal(now,2000);
})().catch(e=>{console.error(e);process.exit(1)});
'''
  r=subprocess.run(['node','-e',script],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
 def test_unreadable_is_not_identity_failure(self):
  block=CONTENT[CONTENT.index('  if (activeCart && !items.length)'):CONTENT.index('  if ((!activeCart || !visible(activeCart))')]
  self.assertIn('unreadable_cart: true',block)
  self.assertNotIn('identity_mismatch: true',block)
  helper=CONTENT[CONTENT.index('async function waitForReadableCart('):CONTENT.index('async function handleCart(')]
  self.assertNotIn('FAIL_JOB',helper);self.assertNotIn('handleClearCart',helper)
  self.assertIn('const cartCheck = await waitForReadableCart(activeJob);\n  if (!cartCheck) return;',CONTENT)
