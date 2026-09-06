(()=>{
  if(location.origin!=='https://secretgreen.com.au'||window.__secretgreenSupportLoaded)return;
  window.__secretgreenSupportLoaded=true;
  // Official LibreDesk launcher, conversation UI and native AI/OTP integrations.
  window.LibredeskSettings={baseURL:'https://libredesk.185.194.236.161.sslip.io',inboxID:'339d24ef-d7ab-4d1a-83ad-f2d0212335e0'};
  const script=document.createElement('script');script.src=window.LibredeskSettings.baseURL+'/widget.js';script.async=true;document.head.appendChild(script);
})();
