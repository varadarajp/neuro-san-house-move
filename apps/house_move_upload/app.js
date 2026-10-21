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

  // ----- After approving: send now, or back to the move -----
  function reviewDone(approved, skipped) {
    const done = el("div", undefined, "done-state");
    done.appendChild(icon("m5 12 5 5 9-10"));
    done.appendChild(el("b", approved + (approved === 1 ? " letter approved" : " letters approved")));
    if (skipped) done.appendChild(el("p", skipped + (skipped === 1 ? " letter skipped." : " letters skipped."), "muted"));
    done.appendChild(el("p", approved ? "Send them now? Complete letters go to the organisation; any still missing "
      + "a reference come back to you as a ready-made pack." : "Nothing approved this time. They stay on your plan.",
      "muted"));
    const send = approved ? button("Send them now", "btn block", () => { close(); act({type: "send"}); }) : null;
    open("Review done", "All letters reviewed", [done], [send, button("Back to my move", "btn ghost block", close)],
      () => HM.refresh && HM.refresh());
  }

  // ----- Approve all: every routine letter in one go; legal requirements stay one at a time -----
  async function approveAll() {
    const status = (await json("/status.json")).body;
    const ids = (status.awaiting_approval || []).map(t => t.task_id);
    if (!ids.length) { toast("No letters are waiting for you."); return; }
    open("Approve all", "Loading your letters…", [el("div", "", "skeleton")], []);
    const drafts = (await Promise.all(ids.map(id => json("/draft/" + encodeURIComponent(id) + ".json"))))
      .filter(r => r.ok).map(r => r.body);
    const routine = drafts.filter(d => !d.legal_requirement), legal = drafts.filter(d => d.legal_requirement);
    const list = el("div", undefined, "approve-list");
    routine.forEach(d => {
      // Each letter can be opened and read here: approving sends back the fingerprint of exactly this wording.
      const item = el("details", undefined, "card");
      const summary = el("summary");
      summary.appendChild(el("b", d.org));
      summary.appendChild(el("span", " · due " + day(d.due_date) + " · " + channel(d.channel), "small muted"));
      item.appendChild(summary);
      item.appendChild(letter(d));
      list.appendChild(item);
    });
    const body = [el("p", routine.length ? "Tap a letter to read it. Highlighted details are filled in from your "
      + "record when it is sent." : "Every letter waiting is a legal requirement.", "small muted"), list];
    if (legal.length) {
      body.push(el("b", legal.length + (legal.length === 1 ? " legal requirement" : " legal requirements")
        + ", approved one at a time", "group-title"));
      const names = el("ul", undefined, "plain-list");
      legal.forEach(d => names.appendChild(el("li", d.org)));
      body.push(names);
    }
    const legalBtn = legal.length ? button("Review the legal ones", "btn secondary block",
      () => review(legal.map(d => d.task_id))) : null;
    const go = routine.length ? button("Approve " + routine.length + (routine.length === 1 ? " letter" : " letters"),
      "btn block", async () => {
        go.disabled = true;
        const res = await post("/approve", {approve: routine.map(d => ({task_id: d.task_id,
          draft_hash: d.draft_hash}))});
        if (!res.ok) { toast("Not approved: " + (res.body.error || res.status), true); go.disabled = false; return; }
        if ((res.body.not_applied || []).length) toast("Not approved: " + res.body.not_applied.join("; "), true);
        if (legal.length) { toast((res.body.approved || []).length + " approved. Now the legal ones."); review(
          legal.map(d => d.task_id)); return; }
        reviewDone((res.body.approved || []).length, 0);
      }) : null;
    const oneByOne = button("Review one by one instead", "btn ghost block", () => review());
    open("Approve all", drafts.length + (drafts.length === 1 ? " letter" : " letters") + " waiting", body,
      [go, legalBtn, oneByOne], () => HM.refresh && HM.refresh());
  }

  // ----- Change: tell the assistant what to rewrite; the new wording needs approving again -----
  function change(d, after) {
    const label = el("label", "What should change?", "field");
    label.htmlFor = "change-text";
    const text = el("textarea");
    text.id = "change-text";
    text.placeholder = "For example: ask them to close the account on the move date, not before.";
    const sendBtn = button("Ask the assistant", "btn block", () => {
      if (!text.value.trim()) return;
      close();
      act({type: "text", text: "Please change the letter to " + d.org + " (" + d.task_id + "): " + text.value.trim()});
    });
    open("Change", d.org, [label, text, el("p", "The assistant rewrites it; you approve the new wording before it "
      + "goes.", "small muted")], [sendBtn, button("Back", "btn ghost block", after)]);
  }

  // ----- Review: one letter at a time: approve, change, skip, or leave for later -----
  async function review(only) {
    const status = (await json("/status.json")).body;
    const waiting = (status.awaiting_approval || []).map(t => t.task_id);
    const queue = only ? waiting.filter(id => only.includes(id)) : waiting;
    if (!queue.length) { toast("No letters are waiting for you."); return; }
    let index = 0, approved = 0, skipped = 0;
    const show = async () => {
      if (index >= queue.length) { reviewDone(approved, skipped); return; }
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
      const later = button("Not now", "btn ghost", () => { index += 1; show(); });
      const row = el("div", undefined, "foot-row");
      row.appendChild(later); row.appendChild(approve);
      const edit = button("Change", "btn secondary grow", () => change(d, show));
      const skip = button("Skip", "btn secondary grow", async () => {
        const warning = d.legal_requirement ? "This is a legal requirement. If you skip it, you must tell "
          + d.org + " yourself. Skip it anyway?" : "Skip this letter? It will not be sent.";
        if (!confirm(warning)) return;
        const res = await post("/skip", {skip: [d.task_id], confirm_legal: !!d.legal_requirement});
        if (res.ok && (res.body.skipped || []).length) skipped += 1;
        else toast("Not skipped: " + (res.body.error || (res.body.not_applied || []).join("; ")), true);
        index += 1; show();
      });
      const more = el("div", undefined, "foot-row");
      more.appendChild(edit); more.appendChild(skip);
      open("Letter " + (index + 1) + " of " + queue.length, d.org, [dots, facts, letter(d), note], [row, more],
        () => HM.refresh && HM.refresh());
    };
    show();
  }

  // ----- Packs: letters the app could not send itself, filled in for the customer to send -----
  async function packs() {
    const status = (await json("/status.json")).body;
    const group = (status.groups || []).find(g => g.status === "with_customer");
    const items = group ? group.tasks : [];
    if (!items.length) { toast("Nothing is waiting for you to send."); return; }
    if (items.length === 1) { pack(items[0].task_id); return; }
    const list = el("ul", undefined, "list");
    items.forEach(t => {
      const li = el("li");
      const row = button("", "row pack-pick", () => pack(t.task_id));
      const main = el("div", undefined, "main");
      main.appendChild(el("div", t.org, "name"));
      main.appendChild(el("div", "Due " + day(t.due_date) + " · " + channel(t.channel), "small muted"));
      row.appendChild(main);
      li.appendChild(row);
      list.appendChild(li);
    });
    open("Send yourself", items.length + " letters ready for you", [el("p", "Each one is filled in with your details. "
      + "Open it, add anything missing, and send it the way shown.", "small muted"), list],
      [button("Back to my move", "btn ghost block", close)], () => HM.refresh && HM.refresh());
  }

  async function pack(taskId) {
    const r = await json("/pack/" + encodeURIComponent(taskId) + ".json");
    if (!r.ok) { toast(r.body.error || "That letter is not waiting for you.", true); return; }
    const p = r.body;
    const facts = el("div", undefined, "facts");
    [["Due", day(p.due_date)], ["How", channel(p.channel)], ["Type", p.legal_requirement ? "Legal" : "Routine"]]
      .forEach(([k, v]) => { const f = el("div", undefined, k === "Type" && p.legal_requirement ? "legal" : "");
        f.appendChild(el("span", k)); f.appendChild(el("b", v)); facts.appendChild(f); });
    const body = [facts];
    const toAdd = (p.to_add || []).concat(p.what_customer_needs || []);
    if (toAdd.length) {
      body.push(el("b", "Add before you send", "group-title"));
      const ul = el("ul", undefined, "plain-list");
      [...new Set(toAdd)].forEach(t => ul.appendChild(el("li", t)));
      body.push(ul);
    }
    // The filled-in letter, with each "[add your ...]" gap highlighted. Built from text nodes only.
    const box = el("div", undefined, "letter");
    box.appendChild(el("div", p.subject || "(no subject)", "subject"));
    const pre = el("pre");
    (p.body || "").split(/(\[add your [^\]]+\])/).forEach(part => {
      pre.appendChild(/^\[add your [^\]]+\]$/.test(part) ? el("span", part, "placeholder")
        : document.createTextNode(part));
    });
    box.appendChild(pre);
    body.push(box);
    const link = /^https:\/\//.test(p.contact_url || "") ? p.contact_url : null;
    body.push(el("p", link ? "Send it with " + p.org + "'s own form or contact page: copy the text, open the page and "
      + "paste or type it in." : "Contact " + p.org + " the way shown, with the text above: no web address is on "
      + "file for it.", "small muted"));
    const copy = button("Copy text", "btn secondary grow", async () => {
      try { await navigator.clipboard.writeText((p.subject ? p.subject + "\n\n" : "") + p.body); toast("Copied."); }
      catch (e) { getSelection().selectAllChildren(pre); toast("Selected: press Copy."); }
    });
    const visit = link ? button("Open " + new URL(link).hostname, "btn secondary grow",
      () => window.open(link, "_blank", "noopener")) : null;
    const tools = el("div", undefined, "foot-row");
    tools.appendChild(copy);
    if (visit) tools.appendChild(visit);
    const done = button("I've sent it", "btn block", async () => {
      if (!confirm("Mark the letter to " + p.org + " as sent? Only do this once it has gone.")) return;
      done.disabled = true;
      const res = await post("/pack-sent", {task_id: p.task_id});
      if (!res.ok) { toast("Not updated: " + (res.body.error || res.status), true); done.disabled = false; return; }
      toast("Marked as sent.");
      close();
    });
    open("Ready for you to send", p.org, body, [tools, done], () => HM.refresh && HM.refresh());
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
    group(res.sent_to_organization.length + " sent to the organisation", res.sent_to_organization, i => i.org);
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
    if (action.type === "sheet") {
      const sheets = {review: () => review(), approve_all: approveAll, packs: packs, upload: upload};
      (sheets[action.sheet] || upload)();
      return;
    }
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

  return {el, button, icon, day, channel, json, post, open, close, review, approveAll, packs, pack, upload, reply,
    sendNow, act, card,
    next,
    refresh: null};
})();
