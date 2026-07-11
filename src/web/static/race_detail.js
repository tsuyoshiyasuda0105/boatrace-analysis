(() => {
  const esc = (value) => String(value ?? "").replace(/[&<>\"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
  const pct = (value) => value == null ? "-" : `${Number(value).toFixed(1)}%`;
  const num = (value, digits = 2) => value == null ? "-" : Number(value).toFixed(digits);
  const posClass = (value) => value ? `f-${Number(value)}` : "";
  const signed = (value, digits = 2) => value == null ? "-" : `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(digits)}`;
  const toneClass = (tone) => tone === "up" ? "is-up" : tone === "down" ? "is-down" : "is-flat";
  const historyCache = new Map();
  const meter = (label, value) => {
    const safeValue = value == null ? null : Math.max(0, Math.min(100, Number(value)));
    return `\n      <div class="motor-meter">\n        <div class="motor-meter-head">\n          <span>${esc(label)}</span>\n          <b>${safeValue == null ? "-" : `${safeValue.toFixed(1)}pt`}</b>\n        </div>\n        <div class="motor-meter-track"><div class="motor-meter-fill" style="width:${safeValue == null ? 0 : safeValue}%;"></div></div>\n      </div>\n    `;
  };
  const trendToneClass = (label) => label === "??" ? "is-up" : label === "??" ? "is-down" : "is-flat";
  const signalLabel = (signal) => {
    const tone = signal?.trend_tone || "flat";
    const value = signal?.trend_value;
    if (tone === "up") return `? ??${value == null ? "" : ` ${signed(value)}`}`;
    if (tone === "down") return `? ??${value == null ? "" : ` ${signed(value)}`}`;
    return "? ???";
  };
  const liftLabel = (signal) => {
    const label = signal?.lift_label || "C";
    const value = signal?.lift_value;
    return `???? ${label}${value == null ? "" : ` ${signed(value)}`}`;
  };
  const renderHistory = (data) => {
    const current = data.current || {};
    const summary = data.summary || {};
    const profile = data.profile || {};
    const liveSignal = data.live_signal || {};
    const rows = data.history || [];
    const title = `M${esc(current.motor_number ?? "-")} ????????`;
    const cycleNote = current.motor_cycle_start ? `???????: ${esc(current.motor_cycle_start)}??` : "???????: ???";
    const summaryHtml = `\n      <div class="motor-history-summary">\n        <div><span>??</span><b>${summary.starts ?? 0}</b></div>\n        <div><span>1??</span><b>${pct(summary.win_rate)}</b></div>\n        <div><span>2??</span><b>${pct(summary.top2_rate)}</b></div>\n        <div><span>3??</span><b>${pct(summary.top3_rate)}</b></div>\n        <div><span>??ST</span><b>${num(summary.avg_start_timing)}</b></div>\n        <div><span>????</span><b>${num(summary.avg_exhibition_time)}</b></div>\n      </div>`;
    const lift = data.lift || {};
    const liftHtml = `\n      <div class="racer-lift-panel">\n        <div class="racer-lift-head">\n          <div class="racer-lift-title">\n            <strong>????????</strong>\n            <span>${esc(lift.note || "?????????????????????????????????")}</span>\n          </div>\n          <div class="motor-profile-badges">\n            <span class="motor-condition-badge ${toneClass(lift.tone)}">${esc(lift.label || "?????")} ${lift.value == null ? "" : signed(lift.value)}</span>\n            <span class="motor-style-chip is-score">?? ${lift.sample_size ?? 0}?</span>\n            <span class="motor-style-chip is-score">${lift.score == null ? "-" : `${Number(lift.score).toFixed(1)}pt`}</span>\n          </div>\n        </div>\n        <div class="racer-lift-grid">\n          <div><span>???</span><b>${signed(lift.exhibition_delta)}</b></div>\n          <div><span>??ST?</span><b>${signed(lift.exhibition_st_delta)}</b></div>\n          <div><span>??ST?</span><b>${signed(lift.start_delta)}</b></div>\n          <div><span>???</span><b>${signed(lift.finish_delta)}</b></div>\n        </div>\n      </div>`;
    const liveSummaryHtml = `\n      <div class="motor-live-summary">\n        <div class="motor-live-chip ${toneClass(liveSignal.trend_tone)}">${esc(signalLabel(liveSignal))}</div>\n        <div class="motor-live-chip is-lift ${toneClass(liveSignal.lift_tone)}">${esc(liftLabel(liveSignal))}</div>\n        <div class="motor-live-chip is-neutral">??T ${num(current.exhibition_time)}</div>\n        <div class="motor-live-chip is-neutral">??ST ${num(current.start_timing_exhibition)}</div>\n      </div>`;
    const profileHtml = `\n      <div class="motor-profile">\n        <div class="motor-profile-head">\n          <div class="motor-profile-title">\n            <strong>????????</strong>\n            <span>${esc(profile.note || "????????ST?????????????")}</span>\n          </div>\n          <div class="motor-profile-badges">\n            <span class="motor-condition-badge ${profile.condition_tone === "up" ? "is-up" : profile.condition_tone === "down" ? "is-down" : "is-flat"}">${esc(profile.condition_label || "??")}</span>\n            <span class="motor-style-chip">${esc(profile.style_label || "?????")}</span>\n            <span class="motor-style-chip is-score">?? ${profile.condition_score == null ? "-" : `${Number(profile.condition_score).toFixed(1)}pt`}</span>\n          </div>\n        </div>\n        <div class="motor-profile-meters">\n          ${meter("??", profile.dash_score)}\n          ${meter("??????", profile.stretch_score)}\n          ${meter("???", profile.turn_score)}\n        </div>\n        <div class="motor-trend-strip">\n          ${(profile.recent_scores || []).map((item) => `\n            <div class="motor-trend-card ${trendToneClass(item.label)}">\n              <span>${esc(item.race_date || "")} ${esc(item.race_number || "")}R</span>\n              <b>${item.score == null ? "-" : `${Number(item.score).toFixed(1)}pt`}</b>\n              <small>${esc(item.label || "")}</small>\n            </div>\n          `).join("")}\n        </div>\n      </div>`;
    if (!rows.length) {
      return `\n        <div class="motor-history-head">\n          <strong>${title}</strong>\n          <span>${esc(current.stadium_name)} / ${esc(current.racer_name)} / ${cycleNote}</span>\n        </div>\n        ${summaryHtml}\n        ${liveSummaryHtml}\n        ${liftHtml}\n        ${profileHtml}\n        <div class="motor-history-empty">?????????????????????????</div>\n      `;
    }
    const body = rows.map((r) => `\n      <tr>\n        <td>${esc(r.race_date)}</td>\n        <td>${esc(r.race_number)}R</td>\n        <td><span class="lane lane-${esc(r.boat_number)} motor-mini-lane">${esc(r.boat_number)}</span></td>\n        <td class="left">${esc(r.racer_name)}<div class="racer-meta">No. ${esc(r.racer_number)}</div></td>\n        <td>${esc(r.course_number ?? "-")}</td>\n        <td>${num(r.start_timing)}</td>\n        <td>${num(r.exhibition_time)}</td>\n        <td>${num(r.start_timing_exhibition)}</td>\n        <td><span class="finish-badge ${posClass(r.finishing_position)}">${esc(r.finishing_position ?? "-")}?</span></td>\n        <td class="left">${esc(r.kimarite || "-")}</td>\n      </tr>\n    `).join("");
    const currentRowHtml = `\n      <tr class="motor-history-current-row">\n        <td>${esc(current.race_date || "")}</td>\n        <td>${esc(current.race_number || "")}R</td>\n        <td><span class="lane lane-${esc(current.boat_number)} motor-mini-lane">${esc(current.boat_number)}</span></td>\n        <td class="left">${esc(current.racer_name || "")}<div class="racer-meta">No. ${esc(current.racer_number || "")}</div></td>\n        <td>-</td>\n        <td>-</td>\n        <td>${num(current.exhibition_time)}</td>\n        <td>${num(current.start_timing_exhibition)}</td>\n        <td><span class="motor-history-inline ${toneClass(liveSignal.trend_tone)}">${esc(signalLabel(liveSignal))}</span></td>\n        <td class="left">-</td>\n      </tr>\n    `;
    return `\n      <div class="motor-history-head">\n        <strong>${title}</strong>\n        <span>${esc(current.stadium_name)} / ?? ${esc(current.boat_number)}?? ${esc(current.racer_name)} / ${cycleNote}</span>\n      </div>\n      ${summaryHtml}\n      ${liveSummaryHtml}\n      ${liftHtml}\n      ${profileHtml}\n      <div class="motor-history-table-wrap">\n        <table class="motor-history-table">\n          <thead>\n            <tr>\n              <th>??</th><th>R</th><th>?</th><th class="left">??</th>\n              <th>??</th><th>ST</th><th>??T</th><th>??ST</th><th>?</th><th class="left">????</th>\n            </tr>\n          </thead>\n          <tbody>${currentRowHtml}${body}</tbody>\n        </table>\n      </div>\n    `;
  };
  const renderInlineSignals = (container, data) => {
    if (!container) return;
    const liveSignal = data.live_signal || {};
    container.innerHTML = `\n      <span class="motor-inline-chip ${toneClass(liveSignal.trend_tone)}">${esc(signalLabel(liveSignal))}</span>\n      <span class="motor-inline-chip is-lift ${toneClass(liveSignal.lift_tone)}">${esc(liftLabel(liveSignal))}</span>\n    `;
  };
  const fetchHistory = (button) => {
    const boatNumber = button.dataset.boatNumber;
    const raceId = button.dataset.raceId;
    const key = `${raceId}:${boatNumber}`;
    if (!historyCache.has(key)) {
      historyCache.set(key, fetch(`/api/race/${encodeURIComponent(raceId)}/motor-history/${boatNumber}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "force-cache",
      }).then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      }).catch((err) => {
        historyCache.delete(key);
        throw err;
      }));
    }
    return historyCache.get(key);
  };
  document.querySelectorAll(".motor-history-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const boatNumber = button.dataset.boatNumber;
      const row = document.getElementById(`motor-history-row-${boatNumber}`);
      const panel = row?.querySelector("[data-motor-history-panel]");
      if (!row || !panel) return;
      if (!row.hidden && panel.dataset.loaded === "1") {
        row.hidden = true;
        button.setAttribute("aria-expanded", "false");
        return;
      }
      row.hidden = false;
      button.setAttribute("aria-expanded", "true");
      if (panel.dataset.loaded === "1") return;
      panel.innerHTML = '<div class="motor-history-loading">????????????...</div>';
      try {
        const data = await fetchHistory(button);
        panel.innerHTML = renderHistory(data);
        renderInlineSignals(document.querySelector(`[data-motor-inline-signals="${boatNumber}"]`), data);
        panel.dataset.loaded = "1";
      } catch (err) {
        panel.innerHTML = `<div class="motor-history-empty">?????????????????${esc(err.message || "")}</div>`;
      }
    });
  });

  const renderMarketSignal = (signal) => {
    if (!signal) return "";
    const roiClass = Number(signal.expected_roi || 0) > 0 ? "ms-roi-positive" : "ms-roi-negative";
    const extras = Array.isArray(signal.extras) ? signal.extras : [];
    return `
      <div class="market-signal market-${esc(signal.tier || "neutral")}">
        <div class="ms-head">
          <span class="ms-title">${esc(signal.title || "")}</span>
          <span class="${roiClass}">想定 ROI ${(Number(signal.expected_roi || 0) * 100).toFixed(1)}%</span>
        </div>
        <div class="ms-msg">${esc(signal.msg || "")}</div>
        ${extras.length ? `
          <div class="ms-extras">
            ${extras.map((ex) => `
              <div class="ms-extra-item">
                <span class="ms-extra-label">${esc(ex.label || "")}</span>
                <span class="ms-extra-msg">${esc(ex.msg || "")}</span>
                ${ex.bet ? `
                  <div class="ms-extra-bet">
                    <strong>買い目:</strong> ${esc(ex.bet)}
                    ${ex.expected_roi != null ? `<span class="ms-extra-roi">(期待値 ${(Number(ex.expected_roi) * 100).toFixed(1)}%)</span>` : ""}
                  </div>
                ` : ""}
              </div>
            `).join("")}
          </div>
        ` : ""}
      </div>
    `;
  };

  const renderNicheSignals = (signals) => {
    if (!Array.isArray(signals) || !signals.length) return "";
    return `
      <div class="niche-signals">
        ${signals.map((sig) => `
          <div class="niche-card niche-${esc(sig.level || "info")}">
            <div class="niche-head">
              <span class="niche-title">${esc(sig.title || "")}</span>
              <span class="niche-boat">
                <span class="lane lane-${esc(sig.boat_number)}">${esc(sig.boat_number)}</span>
                <span class="niche-meta">${esc(sig.class_label || "")} / tilt ${Number(sig.tilt || 0) > 0 ? "+" : ""}${Number(sig.tilt || 0).toFixed(1)}</span>
              </span>
            </div>
            <div class="niche-body">
              <div class="niche-desc">${esc(sig.desc || "")}</div>
              <div class="niche-recommend"><b>推奨:</b> ${esc(sig.recommend || "")}</div>
              ${sig.warning ? `<div class="niche-warning">注意 ${esc(sig.warning)}</div>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
    `;
  };

  const loadRaceSignals = async () => {
    const shell = document.getElementById("race-signal-shell");
    if (!shell) return;
    const raceId = shell.dataset.raceId;
    if (!raceId) return;
    const loading = shell.querySelector("[data-race-signals-loading]");
    const marketContainer = document.getElementById("market-signal-container");
    const nicheContainer = document.getElementById("niche-signals-container");
    try {
      const res = await fetch(`/api/race/${encodeURIComponent(raceId)}/signals`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "force-cache",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (marketContainer) marketContainer.innerHTML = renderMarketSignal(data.market_signal);
      if (nicheContainer) nicheContainer.innerHTML = renderNicheSignals(data.niche_signals);
    } catch (err) {
      if (marketContainer && nicheContainer) {
        marketContainer.innerHTML = "";
        nicheContainer.innerHTML = `<div class="motor-history-empty">シグナル取得に失敗しました: ${esc(err.message || "")}</div>`;
      }
    } finally {
      if (loading) loading.remove();
    }
  };

  loadRaceSignals();
})();
