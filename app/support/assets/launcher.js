(()=>{
  const SITE='https://secretgreen.com.au',BRIDGE='https://fulfilment.gofinch.com',LIBRE='https://libredesk.185.194.236.161.sslip.io';
  if(location.origin!==SITE||window.__secretgreenSupportLoaded)return;
  window.__secretgreenSupportLoaded=true;
  const script=document.createElement('script');
  script.src=LIBRE+'/widget.js';script.async=true;
  script.onload=()=>{
    // Keep LibreDesk's official launcher and conversation UI. Order help is an
    // embedded panel within the same bubble, with no external navigation.
    const widget=window.initLibredesk({baseURL:LIBRE,inboxID:'339d24ef-d7ab-4d1a-83ad-f2d0212335e0'});
    let mode='chat',linked=false;
    const toolbar=document.createElement('div');toolbar.setAttribute('aria-label','Support options');toolbar.setAttribute('role','navigation');
    toolbar.style.cssText='display:none;position:fixed;z-index:10002;box-sizing:border-box;background:#fff;border-bottom:1px solid #e3e8e5;padding:8px 10px;gap:6px;align-items:center;font:14px system-ui;height:52px;border-radius:16px 16px 0 0';
    const chat=document.createElement('button'),order=document.createElement('button'),close=document.createElement('button');
    chat.textContent='General enquiry';order.textContent='Order help';close.textContent='×';close.setAttribute('aria-label','Close support');
    for(const b of [chat,order,close]){b.type='button';b.style.cssText='border:0;border-radius:7px;padding:9px 12px;font:600 13px system-ui;cursor:pointer;background:white;color:#176147';toolbar.appendChild(b)}
    close.style.marginLeft='auto';close.style.fontSize='20px';close.style.padding='3px 9px';
    const panel=document.createElement('iframe');panel.title='Secretgreen order help';panel.referrerPolicy='no-referrer';
    panel.style.cssText='display:none;position:fixed;border:0;background:white;z-index:10001;box-sizing:border-box;border-radius:0 0 16px 16px';
    document.body.append(toolbar,panel);
    function layout(){
      if(!widget.iframe||!widget.isVisible()){toolbar.style.display='none';panel.style.display='none';return}
      const f=widget.iframe;f.style.boxSizing='border-box';f.style.paddingTop='52px';f.style.background='white';
      const r=f.getBoundingClientRect();
      Object.assign(toolbar.style,{display:'flex',top:r.top+'px',left:r.left+'px',width:r.width+'px',borderRadius:widget.isMobile?'0':'16px 16px 0 0'});
      Object.assign(panel.style,{display:mode==='order'?'block':'none',top:(r.top+52)+'px',left:r.left+'px',width:r.width+'px',height:Math.max(0,r.height-52)+'px',borderRadius:widget.isMobile?'0':'0 0 16px 16px'});
      f.style.visibility=mode==='order'?'hidden':'visible';
      for(const [b,selected] of [[chat,mode==='chat'],[order,mode==='order']]){b.style.background=selected?'#eaf3ee':'white';b.setAttribute('aria-pressed',String(selected))}
    }
    chat.onclick=()=>{mode='chat';layout()};
    order.onclick=()=>{mode='order';if(!panel.src)panel.src=BRIDGE+'/public/secretgreen-support?embedded=1';layout()};
    close.onclick=()=>widget.hide();
    widget.onShow(layout);widget.onHide(layout);
    window.addEventListener('resize',()=>requestAnimationFrame(layout));
    window.addEventListener('message',e=>{
      if(e.origin===LIBRE&&e.source===widget.iframe?.contentWindow){
        if(['WIDGET_LOADED','EXPAND_WIDGET','COLLAPSE_WIDGET','CLOSE_WIDGET'].includes(e.data?.type))requestAnimationFrame(layout);
      }
      if(e.origin!==BRIDGE||e.source!==panel.contentWindow||e.data?.type!=='SECRETGREEN_CHAT_LINKED'||linked)return;
      const token=e.data.sessionToken;
      if(typeof token!=='string'||token.length<30||token.length>10000)return;
      linked=true;widget.setCookie(widget.getCookieName('session'),token);
      // Reload the native widget so its session, conversation and WebSocket all
      // initialize together using the verified identity.
      widget.iframe.src=LIBRE+'/widget?inbox_id=339d24ef-d7ab-4d1a-83ad-f2d0212335e0';
      chat.textContent='Chat';mode='chat';layout();
    });
  };
  document.head.appendChild(script);
})();
