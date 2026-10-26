// The extension's panel as an HTML string, from `cbudget panel` (see cbudget/panel.py). Pure, so it is tested with node.
// Two tabs: Detalle (cycle, today, per model, per day, the costliest chats, model budgets) and Límites (every limit as
// an input, the blocked models, the model budgets, the extras and the check). Every text goes through esc(); the page
// runs only its own script (CSP with a nonce). Changes are messages to the extension, which runs `cbudget limits`:
//   {type:"set", key, value} | {type:"block"|"unblock", pattern} | {type:"model", pattern, usd} |
//   {type:"model-remove", pattern} | {type:"tab", tab} | {type:"refresh"} | {type:"report"}
"use strict";

function esc(s) {
  return String(s === undefined || s === null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function money(usd, decimals) { return "$" + Number(usd || 0).toFixed(decimals === undefined ? 2 : decimals); }
function pct(share) { return Math.round(Math.max(0, Math.min(share, 1)) * 100); }
function level(share, warn) { return share >= 1 ? "error" : share >= warn ? "warning" : "ok"; }
function bar(share, lvl) {
  return '<div class="bar"><i class="' + lvl + '" style="width:' + pct(share) + '%"></i></div>';
}
function date(iso) {
  var m = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  var p = String(iso).split("-");
  return p.length === 3 ? Number(p[2]) + " " + m[Number(p[1]) - 1] : esc(iso);
}

function detail(d) {
  var c = d.cycle, t = d.today, share = c.cap ? c.total / c.cap : 0;
  var out = '<section><h2>Uso del ciclo</h2><div class="big">' + money(c.total) + ' <span class="mut">de ' + money(c.cap) +
    "</span></div>" + bar(share, level(share, c.warn)) +
    '<p class="mut">Se reinicia el ' + date(c.reset) + " · quedan " + esc(c.days_left) + " días" +
    (c.complete ? "" : " · solo lo visto desde la instalación") + "</p></section>";
  var tshare = t.block ? t.usd / t.block : 0;
  out += '<section><h2>Hoy</h2><div class="big">' + money(t.usd) + ' <span class="mut">de ' + money(t.block, 0) +
    "</span></div>" + bar(tshare, t.usd >= t.block ? "error" : t.usd >= t.warn ? "warning" : "ok") +
    '<p class="mut">Aviso en ' + money(t.warn, 0) + " · bloqueo en " + money(t.block, 0) + "</p></section>";
  if (d.budgets.length) {
    out += "<section><h2>Presupuestos por modelo</h2><table>";
    d.budgets.forEach(function (b) {
      var s = b.cap ? b.spent / b.cap : 0;
      out += "<tr><td><code>" + esc(b.pattern) + "</code></td><td>" + money(b.spent) + ' <span class="mut">de ' +
        money(b.cap, 0) + "</span></td><td class=\"w\">" + bar(s, level(s, 0.8)) + "</td></tr>";
    });
    out += "</table></section>";
  }
  var top = d.models.length ? d.models[0].usd : 0;
  out += "<section><h2>Gastos por modelo</h2>" + (d.models.length ? "<table>" + d.models.map(function (m) {
    return "<tr><td><code>" + esc(m.model) + "</code></td><td>" + money(m.usd) + '</td><td class="w">' +
      bar(top ? m.usd / top : 0, "ok") + "</td></tr>";
  }).join("") + "</table>" : '<p class="mut">Sin turnos en este ciclo.</p>') + "</section>";
  var peak = Math.max.apply(null, d.days.map(function (x) { return x.usd; }).concat([0]));
  out += '<section><h2>Gastos por día</h2>' + (d.days.length ? '<div class="days">' + d.days.map(function (x) {
    var h = peak ? Math.max(2, Math.round(x.usd / peak * 80)) : 2;
    return '<div class="day" title="' + date(x.date) + ": " + money(x.usd) + '"><i style="height:' + h + 'px"></i><span>' +
      esc(String(x.date).slice(8)) + "</span></div>";
  }).join("") + "</div>" : '<p class="mut">Sin días con gasto.</p>') + "</section>";
  // Cursor's CSV has no chat id: before the hooks' first turn of the cycle there are no chats to rank.
  out += "<section><h2>Chats más caros " + (d.chats_since ? "desde el " + date(d.chats_since) : "del ciclo") + "</h2>" +
    (d.chats_since ? '<p class="mut">Antes, el gasto sale del CSV de Cursor, que no dice de qué chat es.</p>' : "") +
    (d.chats.length ? "<table>" + d.chats.map(function (c) {
    return "<tr><td><code>" + esc(String(c.conversation_id).slice(0, 8)) + "</code></td><td>" + esc(c.model) +
      "</td><td>" + esc(c.turns) + (c.turns === 1 ? " turno" : " turnos") + "</td><td>" + money(c.usd) + "</td></tr>";
  }).join("") + "</table>" : '<p class="mut">Sin chats registrados.</p>') + "</section>";
  return out + '<p><button data-report>Ver el reporte en texto</button></p>';
}

function limitInput(l) {
  var v = l.value, field;
  if (l.kind === "bool") {
    field = '<select data-key="' + esc(l.key) + '"><option value="si"' + (v ? " selected" : "") +
      '>sí</option><option value="no"' + (v ? "" : " selected") + ">no</option></select>";
  } else {
    var shown = l.kind === "share" ? Math.round(v * 100) + "%" : l.kind === "usd" ? Number(v).toFixed(2) : v;
    field = '<input data-key="' + esc(l.key) + '" value="' + esc(shown) + '" size="8">';
  }
  return "<tr><td><code>" + esc(l.key) + "</code>" + (l.custom ? ' <span class="tag">cambiado</span>' : "") +
    "</td><td>" + field + ' <button data-save="' + esc(l.key) + '">Guardar</button></td><td class="mut">' +
    esc(l.help) + "</td></tr>";
}

function limitsTab(d) {
  var out = "<section><h2>Generales</h2><table>" + d.limits.map(limitInput).join("") + "</table>" +
    '<p class="mut">Montos en dólares (12.5), partes con % (80%). Cada cambio queda registrado y te llega una notificación.</p></section>';
  out += "<section><h2>Modelos bloqueados</h2><p>" + (d.blocked_models.length ? d.blocked_models.map(function (p) {
    return '<span class="chip"><code>' + esc(p) + '</code> <button data-unblock="' + esc(p) + '" title="Desbloquear">✕</button></span>';
  }).join(" ") : '<span class="mut">Ninguno</span>') + '</p><p><input id="block-new" placeholder="p. ej. gemini-*" size="18"> ' +
    '<button data-block>Bloquear</button></p></section>';
  out += "<section><h2>Presupuestos por modelo</h2><table>" + d.budgets.map(function (b) {
    return "<tr><td><code>" + esc(b.pattern) + '</code></td><td><input data-budget="' + esc(b.pattern) + '" value="' +
      esc(Number(b.cap).toFixed(2)) + '" size="8"> <button data-budget-save="' + esc(b.pattern) + '">Guardar</button> ' +
      '<button data-budget-remove="' + esc(b.pattern) + '">Quitar</button></td></tr>';
  }).join("") + '<tr><td><input id="budget-pattern" placeholder="claude-opus-*" size="16"></td><td>' +
    '<input id="budget-usd" placeholder="40" size="8"> <button data-budget-new>Agregar</button></td></tr></table></section>';
  out += "<section><h2>Extras del plan (ciclo)</h2><table>" + d.extras.map(function (x) {
    return "<tr><td>" + esc(x.name) + "</td><td>" + money(x.usd) + "</td><td>" +
      (x.granted ? '<span class="tag ok">registrado</span>' : '<span class="mut">sin registrar: pedile al agente «me otorgaron el extra»</span>') +
      "</td></tr>";
  }).join("") + "</table></section>";
  out += "<section><h2>Chequeo</h2>" + (d.problems.length ? "<ul>" + d.problems.map(function (p) {
    return '<li class="' + (p.level === "error" ? "error" : "warning") + '-text">' + esc(p.message) + "</li>";
  }).join("") + "</ul>" : '<p class="ok-text">Sin problemas: los límites son coherentes entre sí.</p>') + "</section>";
  return out;
}

var CSS = "body{font-family:var(--vscode-font-family);font-size:var(--vscode-font-size);color:var(--vscode-foreground);" +
  "background:var(--vscode-editor-background);padding:0 20px 24px;max-width:900px}" +
  "nav{display:flex;gap:4px;border-bottom:1px solid var(--vscode-panel-border);margin:12px 0 8px}" +
  "nav button{background:none;color:var(--vscode-foreground);border:0;border-bottom:2px solid transparent;padding:8px 12px;cursor:pointer;font-size:13px}" +
  "nav button.on{border-bottom-color:var(--vscode-focusBorder);font-weight:600}" +
  "h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--vscode-descriptionForeground);margin:20px 0 8px}" +
  ".big{font-size:22px;font-weight:600}.mut{color:var(--vscode-descriptionForeground)}" +
  ".bar{height:6px;background:var(--vscode-editorWidget-background);border-radius:3px;margin:8px 0;overflow:hidden}" +
  ".bar i{display:block;height:100%;background:var(--vscode-charts-blue)}.bar i.warning{background:var(--vscode-charts-yellow)}" +
  ".bar i.error{background:var(--vscode-charts-red)}table{border-collapse:collapse;width:100%}" +
  "td{padding:5px 8px 5px 0;vertical-align:middle}td.w{width:40%}" +
  ".days{display:flex;align-items:flex-end;gap:3px;height:100px}.day{display:flex;flex-direction:column;align-items:center;flex:1;max-width:24px}" +
  ".day i{display:block;width:100%;background:var(--vscode-charts-blue);border-radius:2px 2px 0 0}.day span{font-size:10px;color:var(--vscode-descriptionForeground)}" +
  "input,select{background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border,transparent);padding:3px 6px}" +
  "button{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:0;padding:4px 10px;cursor:pointer}" +
  ".chip{display:inline-flex;align-items:center;gap:4px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground);padding:2px 4px 2px 8px;border-radius:10px;margin:2px}" +
  ".chip button{background:none;color:inherit;padding:0 4px}.tag{font-size:11px;padding:1px 6px;border-radius:8px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)}" +
  ".error-text,.alert{color:var(--vscode-errorForeground)}.warning-text{color:var(--vscode-editorWarning-foreground)}.ok-text{color:var(--vscode-testing-iconPassed)}" +
  ".alert{border:1px solid var(--vscode-inputValidation-errorBorder);background:var(--vscode-inputValidation-errorBackground);padding:8px 10px;margin:8px 0}" +
  "[hidden]{display:none}";

var SCRIPT = "(function(){const vs=acquireVsCodeApi();const send=m=>vs.postMessage(m);" +
  "document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>{" +
  "document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));" +
  "document.querySelectorAll('[data-pane]').forEach(p=>p.hidden=p.dataset.pane!==b.dataset.tab);send({type:'tab',tab:b.dataset.tab});}));" +
  "const val=s=>{const e=document.querySelector(s);return e?e.value.trim():'';};" +
  "document.addEventListener('click',e=>{const t=e.target.closest('button');if(!t)return;const d=t.dataset;" +
  "if(d.save!==undefined)send({type:'set',key:d.save,value:val('[data-key=\"'+d.save+'\"]')});" +
  "else if(d.unblock!==undefined)send({type:'unblock',pattern:d.unblock});" +
  "else if(d.block!==undefined&&val('#block-new'))send({type:'block',pattern:val('#block-new')});" +
  "else if(d.budgetSave!==undefined)send({type:'model',pattern:d.budgetSave,usd:val('[data-budget=\"'+d.budgetSave+'\"]')});" +
  "else if(d.budgetRemove!==undefined)send({type:'model-remove',pattern:d.budgetRemove});" +
  "else if(d.budgetNew!==undefined&&val('#budget-pattern'))send({type:'model',pattern:val('#budget-pattern'),usd:val('#budget-usd')});" +
  "else if(d.refresh!==undefined)send({type:'refresh'});else if(d.report!==undefined)send({type:'report'});});})();";

// opts: {nonce, csp (the webview's cspSource), tab: "detalle" | "limites", error: a refused change, or ""}
function html(d, opts) {
  var tab = opts.tab === "limites" ? "limites" : "detalle";
  return '<!doctype html><html lang="es"><head><meta charset="utf-8">' +
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\' ' + esc(opts.csp) +
    "; script-src 'nonce-" + esc(opts.nonce) + "';\">" +
    "<style>" + CSS + "</style></head><body>" +
    '<nav><button data-tab="detalle"' + (tab === "detalle" ? ' class="on"' : "") + '>Detalle</button>' +
    '<button data-tab="limites"' + (tab === "limites" ? ' class="on"' : "") + ">Límites</button>" +
    '<span style="flex:1"></span><button data-refresh title="Actualizar">↻</button></nav>' +
    (opts.error ? '<div class="alert">No se guardó: ' + esc(opts.error) + "</div>" : "") +
    '<div data-pane="detalle"' + (tab === "detalle" ? "" : " hidden") + ">" + detail(d) + "</div>" +
    '<div data-pane="limites"' + (tab === "limites" ? "" : " hidden") + ">" + limitsTab(d) + "</div>" +
    '<p class="mut">Estimado por cursor-budget' + (d.version ? " " + esc(d.version) : "") + ". El cobro real lo hace Cursor.</p>" +
    '<script nonce="' + esc(opts.nonce) + '">' + SCRIPT + "</script></body></html>";
}

module.exports = {html: html, esc: esc};
