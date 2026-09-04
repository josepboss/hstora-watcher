(() => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const norm = value => (value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const waitFor = async (find, label, timeout = 20000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const found = find();
      if (found) return found;
      await sleep(250);
    }
    throw new Error(`Timed out waiting for ${label}`);
  };
  const setValue = (element, value) => {
    const proto = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(element, String(value)) : element.value = String(value);
    for (const type of ["input", "change", "blur"]) element.dispatchEvent(new Event(type, {bubbles: true}));
  };
  const selectText = (select, text) => {
    const option = [...select.options].find(o => norm(o.text) === norm(text));
    if (!option) throw new Error(`Option not found: ${text}`);
    select.value = option.value;
    select.dispatchEvent(new Event("change", {bubbles: true}));
  };
  const uniqueSelect = text => [...document.querySelectorAll("select")].find(s => [...s.options].some(o => norm(o.text) === norm(text)));
  const button = text => [...document.querySelectorAll("button")].find(b => norm(b.innerText) === norm(text));
  const checkLabel = text => {
    const label = [...document.querySelectorAll("label")].find(l => norm(l.innerText).includes(norm(text)));
    const input = label && (label.htmlFor ? document.getElementById(label.htmlFor) : label.querySelector('input[type="checkbox"]'));
    if (!input) throw new Error(`Checkbox not found: ${text}`);
    if (!input.checked) input.click();
  };
  const report = payload => new Promise((resolve, reject) => chrome.runtime.sendMessage({type: "REPORT_STATUS", payload}, r => r?.ok ? resolve(r) : reject(new Error(r?.error || "Status update failed"))));

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type !== "Z2U_RUN_ACTION") return;
    performStockAction(message).then(sendResponse);
    return true;
  });

  async function performStockAction({offerIds, action}) {
    try {
      if (!location.pathname.startsWith("/sell/manage")) throw new Error("Z2U redirected away from the listing page");
      const targets = [];
      for (const offerId of offerIds) {
        const checkbox = await waitFor(() => document.querySelector(`input.dataid[value="${cssEscape(offerId)}"]`), `offer #${offerId}`, 15000);
        const row = findOfferContainer(checkbox, offerId);
        const rowText = row?.innerText || "";
        const alreadyInactive = /Status\s*Relist/i.test(rowText);
        const alreadyActive = /Status\s*Deactivate/i.test(rowText);
        if ((action === "deactivate" && alreadyInactive) || (action === "relist" && alreadyActive)) continue;
        targets.push({offerId, checkbox});
      }
      if (!targets.length) return {ok: true, alreadyDone: true};
      for (const {offerId, checkbox} of targets) {
        checkbox.click();
        if (!checkbox.checked) throw new Error(`Could not select offer #${offerId}`);
      }
      const label = action === "deactivate" ? "Deactivate" : "Relist";
      const actionButton = [...document.querySelectorAll("button")].find(b => norm(b.textContent) === norm(label) && visible(b) && !b.disabled);
      if (!actionButton) throw new Error(`${label} button was not found`);
      actionButton.click();
      const submit = await waitFor(() => [...document.querySelectorAll("button")].find(b => norm(b.textContent) === "submit" && visible(b)), "confirmation", 5000);
      submit.click();
      await waitFor(() => [...document.querySelectorAll("body *")].find(n => visible(n) && norm(n.textContent) === "success"), "Z2U success confirmation", 10000);
      return {ok: true};
    } catch (error) { return {ok: false, error: error.message}; }
  }

  function findOfferContainer(checkbox, offerId) {
    let node = checkbox;
    while (node && node !== document.body) {
      if ((node.innerText || "").includes(`#${offerId}`) && node.querySelector?.(`input.dataid[value="${cssEscape(offerId)}"]`)) return node;
      node = node.parentElement;
    }
    return checkbox.parentElement;
  }
  const visible = element => Boolean(element && (element.offsetWidth || element.offsetHeight || element.getClientRects().length));
  const cssEscape = value => globalThis.CSS?.escape ? CSS.escape(String(value)) : String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");

  function showOfferIdPanel(job, message) {
    if (document.getElementById("hstora-z2u-panel")) return;
    const panel = document.createElement("div");
    panel.id = "hstora-z2u-panel";
    panel.innerHTML = `<style>#hstora-z2u-panel{position:fixed;right:20px;bottom:20px;z-index:2147483647;width:350px;padding:18px;border-radius:14px;background:#10141c;color:#fff;box-shadow:0 20px 70px #0009;font:14px system-ui;border:1px solid #65f2ad55}#hstora-z2u-panel strong,#hstora-z2u-panel small{display:block}#hstora-z2u-panel small{color:#9ba3b2;margin:5px 0 12px}#hstora-z2u-panel input{width:100%;box-sizing:border-box;padding:9px;margin-bottom:8px;border:1px solid #343b48;border-radius:7px;background:#080b10;color:white}#hstora-z2u-panel button{width:100%;padding:9px;border:0;border-radius:7px;background:#65f2ad;color:#07110c;font-weight:700;cursor:pointer}</style><strong>HStora → Z2U</strong><small>${message}</small><input class="offer-id" placeholder="Published Z2U offer ID"><input class="manage-url" placeholder="Z2U manage-listing URL"><button>Save offer and stock automation</button>`;
    document.body.appendChild(panel);
    panel.querySelector("button").onclick = async () => {
      const offerId = panel.querySelector(".offer-id").value.trim();
      const manageUrl = panel.querySelector(".manage-url").value.trim();
      if (!/^\d+$/.test(offerId)) return panel.querySelector("small").textContent = "Enter a numeric offer ID.";
      if (!/^https:\/\/www\.z2u\.com\/sell\/manage/.test(manageUrl)) return panel.querySelector("small").textContent = "Enter the Z2U manage-listing URL.";
      try {
        await report({productId: job.productId, status: "published", offerId, manageUrl, listedPrice: job.price});
        panel.querySelector("small").textContent = `Published offer ${offerId} saved to HStora Watcher.`;
        panel.querySelectorAll("input").forEach(input => input.remove()); panel.querySelector("button").remove();
      } catch (error) { panel.querySelector("small").textContent = error.message; }
    };
  }

  async function automate(job) {
    if (!location.pathname.startsWith("/sell/create")) return showOfferIdPanel(job, "Offer submitted. Enter its Z2U offer ID.");
    const search = await waitFor(() => document.querySelector('#keywords[placeholder="What do you want to sell?"]'), "product search");
    if (!norm(search.value).includes("twitter/x accounts")) {
      setValue(search, "twitter");
      const match = await waitFor(() => [...document.querySelectorAll("a")].find(a => norm(a.innerText) === "twitter/x accounts"), "Twitter/X Accounts category");
      match.click();
    }
    const title = await waitFor(() => document.querySelector('input[placeholder="Title"]'), "listing form");
    setValue(title, job.title);
    setValue(document.querySelector('textarea[placeholder="Discription"]'), job.description);
    setValue(document.querySelector('input.cus_fields[data="unit_price"]'), job.price);
    setValue(document.querySelector('input.cus_fields[data="less_num"]'), job.minUnits);
    setValue(document.querySelector('input.cus_fields[data="stock_num"]'), job.stock);
    selectText(uniqueSelect("Twitter"), "Twitter");
    selectText(uniqueSelect("yes"), "yes");
    selectText(uniqueSelect("Other"), "Other");
    const expiration30 = [...document.querySelectorAll("button.zu-radio")].find(b => norm(b.innerText) === "30days");
    if (!expiration30) throw new Error("30-day expiration option not found");
    expiration30.click();
    const delivery = document.querySelector('select[data="online_date"]') || uniqueSelect("15 minute");
    selectText(delivery, "15 minute");
    checkLabel("Order Delivery");
    checkLabel("I have read and agreed");
    await sleep(500);
    const submit = button("Submit");
    if (!submit) throw new Error("Submit button not found");
    submit.click();
    await report({productId: job.productId, status: "submitted", listedPrice: job.price});
    showOfferIdPanel(job, "Offer submitted. When Z2U shows the offer, enter its ID below.");
  }

  chrome.runtime.sendMessage({type: "GET_PENDING"}, response => {
    const job = response?.job;
    if (!job || !["filling", "submitted"].includes(job.status)) return;
    if (job.status === "submitted") return showOfferIdPanel(job, "Offer submitted. Enter its Z2U offer ID.");
    automate(job).catch(async error => {
      showOfferIdPanel(job, `Automation stopped: ${error.message}`);
      try { await report({productId: job.productId, status: "failed", listedPrice: job.price, error: error.message}); } catch {}
    });
  });
})();
