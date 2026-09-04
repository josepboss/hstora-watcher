(() => {
  const announce = () => window.postMessage({source: "hstora-z2u-extension", type: "EXTENSION_READY"}, location.origin);
  announce();
  window.addEventListener("message", event => {
    if (event.source !== window || event.origin !== location.origin || event.data?.source !== "hstora-watcher") return;
    if (event.data.type === "PING_EXTENSION") announce();
    if (event.data.type === "LIST_ON_Z2U" && event.data.listing) {
      chrome.runtime.sendMessage({type: "START_LISTING", listing: event.data.listing}, response => {
        const failed = chrome.runtime.lastError || !response?.ok;
        window.postMessage({
          source: "hstora-z2u-extension",
          type: "Z2U_STATUS",
          status: failed ? "failed" : "filling",
          message: failed ? (chrome.runtime.lastError?.message || response?.error || "Could not start Z2U") : "Z2U opened; filling the offer"
        }, location.origin);
      });
    }
  });
  chrome.runtime.onMessage.addListener(message => {
    if (message.type !== "STATUS") return;
    window.postMessage({source: "hstora-z2u-extension", type: "Z2U_STATUS", status: message.status, message: `Z2U listing ${message.status}`}, location.origin);
  });
})();
