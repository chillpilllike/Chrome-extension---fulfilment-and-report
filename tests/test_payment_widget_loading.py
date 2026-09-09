"""Exercise actual payment orchestration with a delayed Amazon card widget."""
from pathlib import Path
import subprocess
import unittest

CONTENT = (Path(__file__).resolve().parents[1] / 'chrome-extension/content.js').read_text()

class PaymentWidgetLoadingTests(unittest.TestCase):
    def test_delayed_widget_and_payment_guards(self):
        source = CONTENT[CONTENT.index('async function openPaymentSelectionIfAvailable()'):CONTENT.index('async function openAddressEditorIfAvailable(')]
        wait = CONTENT[CONTENT.index('async function waitUntil('):CONTENT.index('function findUseAddressButton()')]
        js = r'''
const assert = require('node:assert/strict');
let now, opened, delay, selected, handled, pauses, canOpen, cardPresent;
Date.now = () => now;
const sleep = async ms => { now += ms; };
const waitIfPaused = async () => {};
const getExtensionState = async () => ({cardLast4Preference:'0902'});
const cardPreferenceList = () => ['0902'];
const findCheckoutPaymentPanel = () => ({});
const checkoutSelectedCardDigits = () => selected;
const findPlaceOrderButton = () => ({});
const findChangePaymentButton = () => canOpen ? {} : null;
const findPaymentRadio = () => opened !== null && now-opened >= delay ? {} : null;
const showPanel = () => {};
const clickElement = async () => { opened = now; };
const pauseForManualCheckout = async (job, message) => {job.paused=true;pauses.push(message);};
const waitForPreferredCheckoutPayment = async () => selected === '0902' ? selected : null;
const handlePaymentSelection = async job => {
  handled++;
  if (cardPresent) selected='0902';
  else await pauseForManualCheckout(job,'Preferred card unavailable');
};
function reset(loadDelay, options={}) {
 now=0;opened=null;delay=loadDelay;selected=options.selected || '3454';
 handled=0;pauses=[];canOpen=options.canOpen !== false;cardPresent=options.cardPresent !== false;
 return {paused:false};
}
'''
        checks = r'''
(async () => {
 for (const ms of [0, 6000, 14000]) {
   const job=reset(ms);
   assert.equal(await ensurePreferredCheckoutPayment(job),true,`load ${ms}`);
   assert.equal(job.paused,false);assert.equal(handled,1);assert.equal(selected,'0902');
   assert.deepEqual(pauses,[]);
 }
 let job=reset(Infinity);
 assert.equal(await ensurePreferredCheckoutPayment(job),false);
 assert.equal(handled,0);assert.equal(job.paused,true);
 assert.match(pauses[0],/did not show card choices/);
 assert.ok(now-opened>=15000 && now-opened<15200,'load timeout stays bounded');
 job=reset(6000,{cardPresent:false});
 assert.equal(await ensurePreferredCheckoutPayment(job),false);
 assert.equal(job.paused,true);assert.equal(selected,'3454');
 job=reset(0,{selected:'0902'});
 assert.equal(await ensurePreferredCheckoutPayment(job),true);
 assert.equal(opened,null);assert.equal(handled,0);
 job=reset(0,{canOpen:false});
 assert.equal(await ensurePreferredCheckoutPayment(job),false);
 assert.equal(job.paused,true);assert.equal(handled,0);
})().catch(e=>{console.error(e);process.exit(1);});
'''
        result = subprocess.run(['node','-e',js+wait+source+checks],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
