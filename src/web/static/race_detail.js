(() => {
  const shell = document.getElementById("race-signal-shell");
  const staticVersion = shell?.dataset?.staticVersion || "v1";

  const esc = (value) => String(value ?? "").replace(/[&<>\"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
  const pct = (value) => value == null ? "-" : `${Number(value).toFixed(1)}%`;
  const num = (value, digits = 2) => value == null ? "-" : Number(value).toFixed(digits);
  const signed = (value, digits = 2) => value == null ? "-" : `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(digits)}`;
  const posClass = (value) => value ? `f-${Number(value)}` : "";
  const toneClass = (tone) => tone === "up" ? "is-up" : tone === "down" ? "is-down" : "is-flat";
  const historyCache = new Map();
  const racerDetailCache = new Map();

  const meter = (label, value) => {
    const safeValue = value == null ? null : Math.max(0, Math.min(100, Number(value)));
    return `
      <div class="motor-meter">
        <div class="motor-meter-head">
          <span>${esc(label)}</span>
          <b>${safeValue == null ? "-" : `${safeValue.toFixed(1)}pt`}</b>
        </div>
        <div class="motor-meter-track"><div class="motor-meter-fill" style="width:${safeValue == null ? 0 : safeValue}%;"></div></div>
      </div>
    `;
  };

  const trendToneClass = (label) => {
    if (label === "rise" || label === "rise_strong" || label === "上向き" || label === "強上昇") return "is-up";
    if (label === "down" || label === "下降") return "is-down";
    return "is-flat";
  };

  const signalLabel = (signal) => {
    const tone = signal?.trend_tone || "flat";
    const value = signal?.trend_value;
    if (tone === "up") return `上昇${value == null ? "" : ` ${signed(value)}`}`;
    if (tone === "down") return `下降${value == null ? "" : ` ${signed(value)}`}`;
    return "横ばい";
  };

  const liftLabel = (signal) => {
    const label = signal?.lift_label || "C";
    const value = signal?.lift_value;
    return `引き出し ${label}${value == null ? "" : ` ${signed(value)}`}`;
  };

  const renderHistory = (data) => {
    const current = data.current || {};
    const summary = data.summary || {};
    const profile = data.profile || {};
    const lift = data.lift || {};
    const liveSignal = data.live_signal || {};
    const rows = data.history || [];
    const title = `M${esc(current.motor_number ?? "-")} 現行期の直近履歴`;
    const cycleNote = current.motor_cycle_start ? `現行モーター期: ${esc(current.motor_cycle_start)}以降` : "現行モーター期: 不明";

    const summaryHtml = `
      <div class="motor-history-summary">
        <div><span>出走</span><b>${summary.starts ?? 0}</b></div>
        <div><span>1着率</span><b>${pct(summary.win_rate)}</b></div>
        <div><span>2連率</span><b>${pct(summary.top2_rate)}</b></div>
        <div><span>3連率</span><b>${pct(summary.top3_rate)}</b></div>
        <div><span>平均ST</span><b>${num(summary.avg_start_timing)}</b></div>
        <div><span>平均展示</span><b>${num(summary.avg_exhibition_time)}</b></div>
      </div>`;

    const liveSummaryHtml = `
      <div class="motor-live-summary">
        <div class="motor-live-chip ${toneClass(liveSignal.trend_tone)}">${esc(signalLabel(liveSignal))}</div>
        <div class="motor-live-chip is-lift ${toneClass(liveSignal.lift_tone)}">${esc(liftLabel(liveSignal))}</div>
        <div class="motor-live-chip is-neutral">展示T ${num(current.exhibition_time)}</div>
        <div class="motor-live-chip is-neutral">展示ST ${num(current.start_timing_exhibition)}</div>
      </div>`;

    const liftHtml = `
      <div class="racer-lift-panel">
        <div class="racer-lift-head">
          <div class="racer-lift-title">
            <strong>選手の引き出し力</strong>
            <span>${esc(lift.note || "選手平均との差分から、モーターをどれだけ引き出しているかを推定")}</span>
          </div>
          <div class="motor-profile-badges">
            <span class="motor-condition-badge ${toneClass(lift.tone)}">${esc(lift.label || "判定保留")} ${lift.value == null ? "" : signed(lift.value)}</span>
            <span class="motor-style-chip is-score">n=${lift.sample_size ?? 0}</span>
            <span class="motor-style-chip is-score">${lift.score == null ? "-" : `${Number(lift.score).toFixed(1)}pt`}</span>
          </div>
        </div>
        <div class="racer-lift-grid">
          <div><span>展示差</span><b>${signed(lift.exhibition_delta)}</b></div>
          <div><span>展示ST差</span><b>${signed(lift.exhibition_st_delta)}</b></div>
          <div><span>本番ST差</span><b>${signed(lift.start_delta)}</b></div>
          <div><span>着順差</span><b>${signed(lift.finish_delta)}</b></div>
        </div>
      </div>`;

    const profileHtml = `
      <div class="motor-profile">
        <div class="motor-profile-head">
          <div class="motor-profile-title">
            <strong>推定モーター気配</strong>
            <span>${esc(profile.note || "展示タイム・展示ST・着順効率からの推定")}</span>
          </div>
          <div class="motor-profile-badges">
            <span class="motor-condition-badge ${toneClass(profile.condition_tone)}">${esc(profile.condition_label || "判定保留")}</span>
            <span class="motor-style-chip">${esc(profile.style_label || "バランス型")}</span>
            <span class="motor-style-chip is-score">総合 ${profile.condition_score == null ? "-" : `${Number(profile.condition_score).toFixed(1)}pt`}</span>
          </div>
        </div>
        <div class="motor-profile-meters">
          ${meter("出足", profile.dash_score)}
          ${meter("行き足・伸び", profile.stretch_score)}
          ${meter("回り足", profile.turn_score)}
        </div>
        <div class="motor-trend-strip">
          ${(profile.recent_scores || []).map((item) => `
            <div class="motor-trend-card ${trendToneClass(item.label)}">
              <span>${esc(item.race_date || "")} ${esc(item.race_number || "")}R</span>
              <b>${item.score == null ? "-" : `${Number(item.score).toFixed(1)}pt`}</b>
              <small>${esc(item.label || "")}</small>
            </div>
          `).join("")}
        </div>
      </div>`;

    if (!rows.length) {
      return `
        <div class="motor-history-head">
          <strong>${title}</strong>
          <span>${esc(current.stadium_name)} / ${esc(current.racer_name)} / ${cycleNote}</span>
        </div>
        ${summaryHtml}
        ${liveSummaryHtml}
        ${liftHtml}
        ${profileHtml}
        <div class="motor-history-empty">モーター履歴の取得に必要な過去データがありません。</div>
      `;
    }

    const currentRowHtml = `
      <tr class="motor-history-current-row">
        <td>${esc(current.race_date || "")}</td>
        <td>${esc(current.race_number || "")}R</td>
        <td><span class="lane lane-${esc(current.boat_number)} motor-mini-lane">${esc(current.boat_number)}</span></td>
        <td class="left">${esc(current.racer_name || "")}<div class="racer-meta">No. ${esc(current.racer_number || "")}</div></td>
        <td>-</td>
        <td>-</td>
        <td>${num(current.exhibition_time)}</td>
        <td>${num(current.start_timing_exhibition)}</td>
        <td><span class="motor-history-inline ${toneClass(liveSignal.trend_tone)}">${esc(signalLabel(liveSignal))}</span></td>
      </tr>
    `;

    const body = rows.map((r) => `
      <tr>
        <td>${esc(r.race_date)}</td>
        <td>${esc(r.race_number)}R</td>
        <td><span class="lane lane-${esc(r.boat_number)} motor-mini-lane">${esc(r.boat_number)}</span></td>
        <td class="left">
          ${esc(r.racer_name)}
          <div class="racer-meta">No. ${esc(r.racer_number)}</div>
          ${r.kimarite ? `<span class="kimarite-mini">${esc(r.kimarite)}</span>` : ""}
        </td>
        <td>${esc(r.course_number ?? "-")}</td>
        <td>${num(r.start_timing)}</td>
        <td>${num(r.exhibition_time)}</td>
        <td>${num(r.start_timing_exhibition)}</td>
        <td><span class="finish-badge ${posClass(r.finishing_position)}">${esc(r.finishing_position ?? "-")}着</span></td>
      </tr>
    `).join("");

    return `
      <div class="motor-history-head">
        <strong>${title}</strong>
        <span>${esc(current.stadium_name)} / ${esc(current.boat_number)}号艇 ${esc(current.racer_name)} / ${cycleNote}</span>
      </div>
      ${summaryHtml}
      ${liveSummaryHtml}
      ${liftHtml}
      ${profileHtml}
      <div class="motor-history-table-wrap">
        <table class="motor-history-table">
          <thead>
            <tr>
              <th>日付</th><th>R</th><th>艇</th><th class="left">選手</th>
              <th>進入</th><th>ST</th><th>展示T</th><th>展示ST</th><th>着</th>
            </tr>
          </thead>
          <tbody>${currentRowHtml}${body}</tbody>
        </table>
      </div>
    `;
  };

  const courseStatsGrid = (title, rows) => `
    <div class="racer-course-block">
      <div class="racer-course-title">${esc(title)}</div>
      <div class="racer-course-grid">
        ${(rows || []).map((r) => `
          <div class="racer-course-card">
            <span>${esc(r.course)}C</span>
            <b>${pct(r.win_rate)}</b>
            <small>${esc(r.wins ?? 0)}/${esc(r.starts ?? 0)}勝</small>
            <em>直近10ST ${r.recent10_avg_st == null ? "-" : Number(r.recent10_avg_st).toFixed(3)} (${esc(r.recent10_st_n ?? 0)})</em>
          </div>
        `).join("")}
      </div>
    </div>
  `;

  const renderRacerDetail = (data) => {
    const current = data.current || {};
    return `
      <div class="racer-detail-head">
        <div>
          <strong>${esc(current.racer_name || "-")}</strong>
          <span>No. ${esc(current.racer_number || "-")} / ${esc(current.class_label || "-")} / ${esc(current.stadium_name || "-")}</span>
        </div>
        <small>${esc(data.note || "")}</small>
      </div>
      ${courseStatsGrid(`${esc(current.stadium_name || "当地")} 1C-6C 1着率`, data.venue_courses)}
      ${courseStatsGrid("全会場 1C-6C 1着率", data.national_courses)}
    `;
  };

  const renderInlineSignals = (container, data) => {
    if (!container) return;
    const liveSignal = data.live_signal || {};
    container.innerHTML = `
      <span class="motor-inline-chip ${toneClass(liveSignal.trend_tone)}">${esc(signalLabel(liveSignal))}</span>
      <span class="motor-inline-chip is-lift ${toneClass(liveSignal.lift_tone)}">${esc(liftLabel(liveSignal))}</span>
    `;
  };

  const fetchHistory = (button) => {
    const boatNumber = button.dataset.boatNumber;
    const raceId = button.dataset.raceId;
    const key = `${raceId}:${boatNumber}`;
    if (!historyCache.has(key)) {
      historyCache.set(key, fetch(`/api/race/${encodeURIComponent(raceId)}/motor-history/${boatNumber}?v=${encodeURIComponent(staticVersion)}`, {
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

  const fetchRacerDetail = (button) => {
    const boatNumber = button.dataset.boatNumber;
    const raceId = button.dataset.raceId;
    const key = `${raceId}:${boatNumber}`;
    if (!racerDetailCache.has(key)) {
      racerDetailCache.set(key, fetch(`/api/race/${encodeURIComponent(raceId)}/racer-detail/${boatNumber}?v=${encodeURIComponent(staticVersion)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "force-cache",
      }).then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      }).catch((err) => {
        racerDetailCache.delete(key);
        throw err;
      }));
    }
    return racerDetailCache.get(key);
  };

  document.querySelectorAll(".racer-detail-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const boatNumber = button.dataset.boatNumber;
      const row = document.getElementById(`racer-detail-row-${boatNumber}`);
      const panel = row?.querySelector("[data-racer-detail-panel]");
      if (!row || !panel) return;
      if (!row.hidden && panel.dataset.loaded === "1") {
        row.hidden = true;
        button.setAttribute("aria-expanded", "false");
        return;
      }
      row.hidden = false;
      button.setAttribute("aria-expanded", "true");
      if (panel.dataset.loaded === "1") return;
      panel.innerHTML = '<div class="motor-history-loading">選手詳細を読み込み中...</div>';
      try {
        const data = await fetchRacerDetail(button);
        panel.innerHTML = renderRacerDetail(data);
        panel.dataset.loaded = "1";
      } catch (err) {
        panel.innerHTML = `<div class="motor-history-empty">選手詳細の取得に失敗しました: ${esc(err.message || "")}</div>`;
      }
    });
  });

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
      panel.innerHTML = '<div class="motor-history-loading">モーター履歴を読み込み中...</div>';
      try {
        const data = await fetchHistory(button);
        panel.innerHTML = renderHistory(data);
        renderInlineSignals(document.querySelector(`[data-motor-inline-signals="${boatNumber}"]`), data);
        panel.dataset.loaded = "1";
      } catch (err) {
        panel.innerHTML = `<div class="motor-history-empty">モーター履歴の取得に失敗しました: ${esc(err.message || "")}</div>`;
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
          <span class="${roiClass}">想定ROI ${(Number(signal.expected_roi || 0) * 100).toFixed(1)}%</span>
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
              ${sig.warning ? `<div class="niche-warning">注意: ${esc(sig.warning)}</div>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
    `;
  };

  const loadRaceSignals = async () => {
    if (!shell) return;
    const raceId = shell.dataset.raceId;
    if (!raceId) return;
    const loading = shell.querySelector("[data-race-signals-loading]");
    const marketContainer = document.getElementById("market-signal-container");
    const nicheContainer = document.getElementById("niche-signals-container");
    try {
      const res = await fetch(`/api/race/${encodeURIComponent(raceId)}/signals?v=${encodeURIComponent(staticVersion)}`, {
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

  const scheduleRaceSignals = () => {
    if (!shell) return;
    const run = () => {
      loadRaceSignals();
    };
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(run, { timeout: 1500 });
    } else {
      window.setTimeout(run, 450);
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleRaceSignals, { once: true });
  } else {
    scheduleRaceSignals();
  }
})();
