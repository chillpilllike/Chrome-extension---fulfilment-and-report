/** Official LibreDesk loader. Credentials and signing secrets stay on the server. */
(async function () {
    if (window.__libredeskOdooLoading) return;
    window.__libredeskOdooLoading = true;
    try {
        const response = await fetch('/libredesk/widget-config', {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok) return;
        const config = await response.json();
        if (!config.inboxID || !config.baseURL) return;
        if (window.Libredesk) {
            if (config.userJWT && window.LibredeskSettings?.inboxID === config.inboxID) window.Libredesk.setUser(config.userJWT);
            return;
        }
        window.LibredeskSettings = config;
        if (document.querySelector('script[data-libredesk-odoo]')) return;
        const script = document.createElement('script');
        script.dataset.libredeskOdoo = '1';
        script.src = config.baseURL + '/widget.js';
        script.async = true;
        document.body.appendChild(script);
    } catch (_) { /* A chat outage must not interrupt shopping or checkout. */ }
})();
