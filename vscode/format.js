// The status bar text and its colour level, from `cbudget statusline --json`. Pure, so it is tested with node alone.
"use strict";

function money(usd, decimals) {
  return "$" + Number(usd).toFixed(decimals);
}

// warning: the day crossed warn_day or the cycle its first warning share; error: the day reached block_day or the
// cycle its cap.
function label(s) {
  var level = "ok";
  if (s.today >= s.block_day || s.cycle >= s.cap) level = "error";
  else if (s.today >= s.warn_day || s.cycle >= s.cap * s.cycle_warn) level = "warning";
  var text = "$(pulse) hoy " + money(s.today, 2) + " de " + money(s.block_day, 0) +
             " · ciclo " + money(s.cycle, 0) + " de " + money(s.cap, 0);
  return {text: text, level: level};
}

module.exports = {label: label};
