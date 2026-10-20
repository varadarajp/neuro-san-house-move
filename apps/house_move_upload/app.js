// House Move app: the guided flow shared by the Move and Chat tabs. Served at /app.js.
// Sheets (review, upload, reply) open over the current screen; next-step actions run the same way everywhere.
// Everything is built with DOM nodes and textContent: nothing from the case is ever interpreted as HTML.
"use strict";

const HM = (() => {
  const el = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text !== undefined && text !== null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };
  const button = (text, cls, onClick) => {
    const b = el("button", text, cls);
    b.type = "button";
    b.onclick = onClick;
    return b;
  };
  const day = (iso) => iso ? new Date(iso + "T12:00:00").toLocaleDateString("en-GB", {day: "numeric", month: "short"})
    : "Not set";
  const channel = (c) => ({email: "Email", letter: "Letter", phone: "Phone", online_form: "Online form",
    direct: "Done by the bank"})[c] || (c || "").replace(/_/g, " ");
  const json = async (url, options) => {
    const r = await fetch(url, Object.assign({cache: "no-store"}, options || {}));
    if (r.status === 401) { location.href = "/"; throw new Error("signed out"); }
    return {ok: r.ok, status: r.status, body: await r.json().catch(() => ({}))};
  };
  const post = (url, body) => json(url, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)});

  // ----- The sheet: one at a time, over whatever screen is open -----
  let sheet = null, scrim = null, lastFocus = null, onClose = null;
  function ensureSheet() {
    if (sheet) return;
    scrim = el("div", undefined, "scrim");
    scrim.onclick = () => close();
    sheet = el("section", undefined, "sheet");
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-hidden", "true");
    document.body.appendChild(scrim);
    document.body.appendChild(sheet);
    addEventListener("keydown", e => { if (e.key === "Escape" && sheet.classList.contains("open")) close(); });
  }
  function open(kicker, title, bodyNodes, footNodes, whenClosed) {
    ensureSheet();
    lastFocus = document.activeElement;
    onClose = whenClosed || null;
    sheet.textContent = "";
    sheet.appendChild(el("div", "", "grab"));
    const head = el("header");
    const titles = el("div", undefined, "titles");
    if (kicker) titles.appendChild(el("span", kicker, "kicker"));
    const h = el("h2", title);
    h.id = "sheet-title";
    titles.appendChild(h);
    head.appendChild(titles);
    const x = button("", "icon-btn", () => close());
    x.setAttribute("aria-label", "Close");
    x.appendChild(icon("M6 6l12 12M18 6 6 18"));
    head.appendChild(x);
    sheet.appendChild(head);
    sheet.setAttribute("aria-labelledby", "sheet-title");
    const body = el("div", undefined, "body");
    bodyNodes.forEach(n => n && body.appendChild(n));
    sheet.appendChild(body);
    const foot = el("footer");
    footNodes.forEach(n => n && foot.appendChild(n));
    sheet.appendChild(foot);
    sheet.classList.add("open");
    sheet.setAttribute("aria-hidden", "false");
    scrim.classList.add("open");
    document.body.style.overflow = "hidden";
    setTimeout(() => (foot.querySelector("button") || x).focus(), 60);
  }
  function close() {
    if (!sheet) return;
    sheet.classList.remove("open");
    sheet.setAttribute("aria-hidden", "true");
    scrim.classList.remove("open");
    document.body.style.overflow = "";
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
    const then = onClose;
    onClose = null;
    if (then) then();
  }
  function icon(d) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("aria-hidden", "true");
    d.split("|").forEach(p => { const path = document.createElementNS(s.namespaceURI, "path");
      path.setAttribute("d", p); s.appendChild(path); });
    return s;
  }

  // ----- A letter, with {{placeholders}} shown as highlighted labels -----
  function letter(d) {
    const box = el("div", undefined, "letter");
    box.appendChild(el("div", d.subject || "(no subject)", "subject"));
    const pre = el("pre");
    (d.body || "").split(/(\{\{\s*\w+\s*\}\})/).forEach(part => {
      const m = part.match(/^\{\{\s*(\w+)\s*\}\}$/);
      pre.appendChild(m ? el("span", m[1].replace(/_/g, " "), "placeholder") : document.createTextNode(part));
    });
    box.appendChild(pre);
    return box;
  }

  // ----- Review: one letter at a time, Approve and next -----
  async function review() {
    const status = (await json("/status.json")).body;
    const queue = (status.awaiting_approval || []).map(t => t.task_id);
    if (!queue.length) { toast("No letters are waiting for you."); return; }
    let index = 0, approved = 0;
    const showDone = () => {
      const done = el("div", undefined, "done-state");
      done.appendChild(icon("m5 12 5 5 9-10"));
      done.appendChild(el("b", approved + (approved === 1 ? " letter approved" : " letters approved")));
      done.appendChild(el("p", approved ? "Send them now? Complete letters go to the organisation; any still missing "
        + "a reference come back to you as a ready-made pack." : "Nothing approved this time. They stay on your plan.",
        "muted"));
      const send = approved ? button("Send them now", "btn block", () => { close(); act({type: "send"}); }) : null;
      open("Review done", "All letters reviewed", [done], [send, button("Back to my move", "btn ghost block", close)],
        () => HM.refresh && HM.refresh());
    };
    const show = async () => {
      if (index >= queue.length) { showDone(); return; }
      const r = await json("/draft/" + encodeURIComponent(queue[index]) + ".json");
      if (!r.ok) { index += 1; show(); return; }
      const d = r.body;
      const dots = el("div", undefined, "dots-row");
      queue.forEach((_, i) => dots.appendChild(el("i", "", i < index ? "done" : i === index ? "now" : "")));
      const facts = el("div", undefined, "facts");
      [["Due", day(d.due_date)], ["How", channel(d.channel)], ["Type", d.legal_requirement ? "Legal" : "Routine"]]
        .forEach(([k, v]) => { const f = el("div", undefined, k === "Type" && d.legal_requirement ? "legal" : "");
          f.appendChild(el("span", k)); f.appendChild(el("b", v)); facts.appendChild(f); });
      const note = el("p", "Highlighted details are filled in from your record when it is sent.", "small muted");
      const approve = button(index === queue.length - 1 ? "Approve" : "Approve and next", "btn grow", async () => {
        approve.disabled = true;
        const res = await post("/approve", {approve: [{task_id: d.task_id, draft_hash: d.draft_hash}]});
        if (res.ok && (res.body.approved || []).length) approved += 1;
        else toast("Not approved: " + (res.body.error || (res.body.not_applied || []).join("; ")), true);
        index += 1; show();
      });
      const skip = button("Not now", "btn ghost", () => { index += 1; show(); });
      const row = el("div", undefined, "foot-row");
      row.appendChild(skip); row.appendChild(approve);
      open("Letter " + (index + 1) + " of " + queue.length, d.org, [dots, facts, letter(d), note], [row],
        () => HM.refresh && HM.refresh());
    };
    show();
  }

  // ----- Upload: a statement into the signed-in person's own folder, then tell the assistant -----
  function upload() {
    const input = el("input");
    input.type = "file"; input.accept = ".csv,.ofx,.qfx"; input.id = "statement";
    const label = el("label", undefined, "drop");
    label.htmlFor = "statement";
    label.appendChild(icon("M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8Z|M14 3v5h5"));
    const name = el("span", "Choose a CSV, OFX or QFX file", "file-name");
    label.appendChild(name);
    label.appendChild(el("span", "Up to 5 MB, downloaded from your online banking", "small muted"));
    label.appendChild(input);
    const go = button("Upload and use it", "btn block", async () => {
      const file = input.files[0];
      if (!file) return;
      go.disabled = true; go.textContent = "Uploading…";
      const form = new FormData(); form.append("file", file);
      const r = await fetch("/upload.json", {method: "POST", body: form});
      const res = await r.json().catch(() => ({}));
      if (!r.ok) { toast(res.error || "That file could not be used.", true); go.disabled = false;
        go.textContent = "Upload and use it"; return; }
      close();
      toast(res.rows + " transactions found.");
      act({type: "say", say: "uploaded", file: res.file_name});
    });
    go.disabled = true;
    input.onchange = () => { name.textContent = input.files[0] ? input.files[0].name : "Choose a file";
      label.classList.toggle("has-file", !!input.files[0]); go.disabled = !input.files[0]; };
    const points = el("ul", undefined, "plain-list small muted");
    ["It goes to your own private folder, never into the chat.", "Read once to find who you pay, then deleted.",
     "The assistant asks your permission before reading it."].forEach(t => points.appendChild(el("li", t)));
    open("Share who you pay", "Upload a bank statement", [label, points], [go]);
  }

  // ----- Reply: answer what a provider asked for -----
  function reply(asks) {
    const fields = [];
    const body = (asks || []).map((a, i) => {
      const box = el("div", undefined, "ask");
      box.appendChild(el("b", a.org));
      box.appendChild(el("p", "Asked for: " + a.asked, "small muted"));
      const l = el("label", "Your answer", "field"); l.htmlFor = "ask" + i;
      const input = el("input"); input.id = "ask" + i; input.type = "text";
      fields.push({a, input});
      box.appendChild(l); box.appendChild(input);
      return box;
    });
    const sendBtn = button("Send to the assistant", "btn block", () => {
      const lines = fields.filter(f => f.input.value.trim())
        .map(f => f.a.org + " asked for " + f.a.asked + ": " + f.input.value.trim());
      if (!lines.length) return;
      close();
      act({type: "text", text: lines.join("\n") + "\nPlease prepare the replies for my approval."});
    });
    open("Reply needed", "A provider needs something", body.concat([el("p",
      "The assistant drafts the reply; you approve it before it goes.", "small muted")]), [sendBtn]);
  }

  // ----- Send: every approved letter goes now, through the same gate as the agents -----
  async function sendNow() {
    open("Sending", "Sending your approved letters…", [el("div", "", "skeleton")], []);
    const r = await post("/send", {});
    if (!r.ok) { close(); toast("Not sent: " + (r.body.error || r.status), true); return; }
    const res = r.body, body = [];
    const group = (title, items, line) => {
      if (!items.length) return;
      body.push(el("b", title, "group-title"));
      const ul = el("ul", undefined, "plain-list");
      items.forEach(i => ul.appendChild(el("li", line(i))));
      body.push(ul);
    };
    group(res.sent_to_organisation.length + " sent to the organisation", res.sent_to_organisation, i => i.org);
    group(res.packs_for_customer.length + " ready for you to send", res.packs_for_customer,
      i => i.org + (i.customer_to_complete && i.customer_to_complete.length ? ": add " + i.customer_to_complete.join(", ")
        : ""));
    if (res.bank_update) body.push(el("p", "Your address with us changes on " + day(res.bank_update.effective_date) + ".",
      "small"));
    group(res.refused.length + " not sent", res.refused, i => i);
    if (!body.length) body.push(el("p", "Nothing was approved and waiting to go.", "muted"));
    open("Sent", "Your letters are on their way", body, [button("Back to my move", "btn block", close)],
      () => HM.refresh && HM.refresh());
  }

  // ----- Running any next-step action, from the Move hero or a chat card -----
  const ON_CHAT = () => typeof window.chatSay === "function";
  function act(action, next) {
    if (!action) return;
    if (action.type === "link") { location.href = action.target; return; }
    if (action.type === "sheet") { action.sheet === "review" ? review() : upload(); return; }
    if (action.type === "reply") { reply((next || {}).asks); return; }
    if (action.type === "send") { sendNow(); return; }
    if (action.type === "say" || action.type === "text") {
      if (ON_CHAT()) { window.chatSay(action); return; }
      const params = new URLSearchParams({do: action.type === "say" ? action.say : "text"});
      if (action.file) params.set("file", action.file);
      if (action.type === "text") sessionStorage.setItem("hm_text", action.text);
      location.href = "/chat?" + params.toString();
    }
  }

  // ----- A next-step card: the Move hero, or a card under the assistant's reply -----
  function card(next, variant) {
    const c = el("div", undefined, "next-card " + (variant || "") + (next.key === "done" ? " done" : ""));
    c.appendChild(el("span", next.key === "done" ? "All done" : "Your next step", "kicker"));
    c.appendChild(el("b", next.title, "next-title"));
    if (next.key === "follow" && next.asks && next.asks.length) {
      next.asks.forEach(a => c.appendChild(el("p", a.org + " asked for " + a.asked, "next-hint")));
    } else {
      c.appendChild(el("p", next.hint, "next-hint"));
    }
    if (next.action) c.appendChild(button(next.label, "btn block next-btn", () => act(next.action, next)));
    if (next.secondary) c.appendChild(button(next.secondary.label, "btn ghost block", () => act(next.secondary)));
    return c;
  }
  async function next() { return (await json("/next.json")).body; }

  return {el, button, icon, day, channel, json, post, open, close, review, upload, reply, sendNow, act, card, next,
    refresh: null};
})();
