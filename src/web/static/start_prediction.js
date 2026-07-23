(() => {
  const root = document.querySelector("[data-start-prediction]");
  if (!root) return;

  const raceId = root.dataset.raceId;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const pct = (v) => `${(Number(v || 0) * 100).toFixed(1)}%`;
  const num = (v, d = 3) => (v == null || v === "" ? "-" : Number(v).toFixed(d));
  const lane = (n) => `<span class="lane lane-${esc(n)}">${esc(n)}</span>`;
  const stageLabel = { pre_exhibition: "展示前", post_exhibition: "展示後" };
  const stageSub = {
    pre_exhibition: "前日・朝時点の選手、モーター、天候、潮で作る仮説",
    post_exhibition: "展示ST、展示タイム、進入を反映した直前補正",
  };

  const bestBoat = (prediction, key) => {
    const boats = prediction?.boats || [];
    if (!boats.length) return null;
    return boats.reduce((best, row) => (
      Number(row[key] || 0) > Number(best[key] || 0) ? row : best
    ), boats[0]);
  };
  const boatMap = (prediction) => Object.fromEntries(
    (prediction?.boats || []).map((b) => [Number(b.boat_number), b]),
  );
  const actualMap = (actual) => Object.fromEntries(
    (actual?.results || []).map((r) => [Number(r.boat_number), r]),
  );

  const renderBoatTrack = (prediction, actual) => {
    const boats = prediction?.boats || [];
    const resultByBoat = actualMap(actual);
    const ordered = boats.length
      ? [...boats].sort((a, b) => Number(a.predicted_start_rank || 9) - Number(b.predicted_start_rank || 9))
      : [1, 2, 3, 4, 5, 6].map((boat) => ({ boat_number: boat }));
    return `<div class="start-boat-track">
      ${ordered.map((b, idx) => {
        const boat = Number(b.boat_number);
        const result = resultByBoat[boat];
        const finish = result?.finishing_position ? `${result.finishing_position}着` : "";
        const actualSt = result?.start_timing == null ? "" : `本番ST ${num(result.start_timing, 2)}`;
        return `<div class="start-boat-node${idx === 0 ? " is-front" : ""}">
          <div class="start-boat-arrow">${lane(boat)}<span>→</span></div>
          <div class="start-boat-meta">
            <b>${idx + 1}位予測</b>
            <span>予測ST ${num(b.predicted_st)}</span>
            <span>STトップ ${pct(b.start_top_probability)}</span>
            ${finish || actualSt ? `<small>${esc(finish)} ${esc(actualSt)}</small>` : ""}
          </div>
        </div>`;
      }).join("")}
    </div>`;
  };

  const renderStageCard = (stage, prediction, actual) => {
    if (!prediction) {
      return `<article class="start-stage-card is-empty" data-stage-card="${stage}">
        <div class="start-stage-title"><span>${esc(stageLabel[stage])}</span><b>未生成</b></div>
        <p>${esc(stageSub[stage])}</p>
        <button type="button" data-generate-start="${stage}">${esc(stageLabel[stage])}を生成</button>
      </article>`;
    }
    const leader = bestBoat(prediction, "first_probability");
    const startTop = bestBoat(prediction, "start_top_probability");
    const tri = (prediction.trifectas || []).slice(0, 5).map((x) => {
      const key = x.scenario_key || x.combination;
      return `<li><b>${esc(key)}</b><span>${pct(x.probability)}</span></li>`;
    }).join("");
    return `<article class="start-stage-card" data-stage-card="${stage}">
      <div class="start-stage-title"><span>${esc(stageLabel[stage])}</span><b>信頼度 ${pct(prediction.confidence)}</b></div>
      <p>${esc(stageSub[stage])}</p>
      ${renderBoatTrack(prediction, actual)}
      <div class="start-stage-facts">
        <div><span>ST先行</span><b>${startTop ? `${lane(startTop.boat_number)} ${pct(startTop.start_top_probability)}` : "-"}</b></div>
        <div><span>1M先頭</span><b>${prediction.first_mark_boat ? `${lane(prediction.first_mark_boat)} ${pct(prediction.first_mark_probability)}` : "-"}</b></div>
        <div><span>決まり手</span><b>${esc(prediction.predicted_kimarite || "-")} ${pct(prediction.kimarite_probability)}</b></div>
        <div><span>1着最有力</span><b>${leader ? `${lane(leader.boat_number)} ${pct(leader.first_probability)}` : "-"}</b></div>
      </div>
      <ol class="start-mini-trifectas">${tri}</ol>
    </article>`;
  };

  const renderActualCard = (actual, postPrediction) => {
    if (!actual) {
      return `<article class="start-stage-card is-empty is-actual">
        <div class="start-stage-title"><span>本番結果</span><b>未確定</b></div>
        <p>結果取得後に、本番ST、着順、決まり手と予測のズレを照合します。</p>
        <button type="button" data-evaluate-start>結果と照合</button>
      </article>`;
    }
    const resultByBoat = actualMap(actual);
    const predictionByBoat = boatMap(postPrediction);
    const rows = [1, 2, 3, 4, 5, 6].map((boat) => {
      const result = resultByBoat[boat] || {};
      const pred = predictionByBoat[boat] || {};
      const stDiff = result.start_timing == null || pred.predicted_st == null
        ? null
        : Number(result.start_timing) - Number(pred.predicted_st);
      const tone = stDiff == null ? "" : stDiff <= -0.02 ? " is-good" : stDiff >= 0.02 ? " is-bad" : " is-flat";
      return `<tr>
        <td>${lane(boat)}</td>
        <td>${result.finishing_position ? `${result.finishing_position}着` : "-"}</td>
        <td>${num(result.start_timing, 2)}</td>
        <td>${num(pred.predicted_st)}</td>
        <td class="start-diff${tone}">${stDiff == null ? "-" : `${stDiff >= 0 ? "+" : ""}${stDiff.toFixed(3)}`}</td>
        <td>${esc(result.kimarite || "-")}</td>
      </tr>`;
    }).join("");
    return `<article class="start-stage-card is-actual">
      <div class="start-stage-title"><span>本番結果</span><b>${esc(actual.actual_combo || "結果確定")}</b></div>
      <div class="start-result-summary">
        <div><span>1着</span><b>${actual.actual_first_boat ? lane(actual.actual_first_boat) : "-"}</b></div>
        <div><span>STトップ</span><b>${actual.actual_start_top_boat ? lane(actual.actual_start_top_boat) : "-"}</b></div>
        <div><span>決まり手</span><b>${esc(actual.actual_kimarite || "-")}</b></div>
      </div>
      <div class="start-table-wrap"><table class="start-table start-result-table"><thead><tr><th>艇</th><th>着</th><th>本番ST</th><th>予測ST</th><th>差</th><th>決まり手</th></tr></thead><tbody>${rows}</tbody></table></div>
    </article>`;
  };

  const renderComparison = (data) => {
    const pre = data.pre_exhibition;
    const post = data.post_exhibition;
    const actual = data.actual;
    root.innerHTML = `<div class="start-prediction-head">
      <div>
        <span class="start-eyebrow">START FLOW ANALYSIS</span>
        <h3>展開予測タイムライン</h3>
        <p>展示前の仮説、展示後の補正、本番結果を同じ流れで比較します。</p>
      </div>
      <div class="start-action-row">
        <button type="button" data-generate-start="pre_exhibition">展示前生成</button>
        <button type="button" data-generate-start="post_exhibition">展示後生成</button>
        <button type="button" data-evaluate-start>結果照合</button>
      </div>
    </div>
    <div class="start-flow-grid">
      ${renderStageCard("pre_exhibition", pre, actual)}
      ${renderStageCard("post_exhibition", post, actual)}
      ${renderActualCard(actual, post || pre)}
    </div>
    <div class="start-comparison-note">選手ごとの傾向は、予測STと本番STの差、STトップ確率、決まり手のズレから蓄積して精度改善に使います。利益を保証するものではありません。</div>`;
  };

  const loadTimeline = async () => {
    const res = await fetch(`/api/predictions/races/${encodeURIComponent(raceId)}/timeline`, { credentials: "same-origin" });
    if (!res.ok) throw new Error("timeline not found");
    renderComparison(await res.json());
  };

  const setBusy = (button, text) => {
    if (!button) return;
    button.disabled = true;
    button.dataset.originalText = button.textContent;
    button.textContent = text;
  };
  const clearBusy = (button) => {
    if (!button) return;
    button.disabled = false;
    button.textContent = button.dataset.originalText || button.textContent;
  };

  root.addEventListener("click", async (event) => {
    const generateButton = event.target.closest("[data-generate-start]");
    const evaluateButton = event.target.closest("[data-evaluate-start]");
    try {
      if (generateButton) {
        const stage = generateButton.dataset.generateStart || "post_exhibition";
        setBusy(generateButton, "生成中...");
        const res = await fetch(`/api/predictions/races/${encodeURIComponent(raceId)}`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stage }),
        });
        if (!res.ok) throw new Error("prediction generation failed");
        await loadTimeline();
      }
      if (evaluateButton) {
        setBusy(evaluateButton, "照合中...");
        const res = await fetch(`/api/predictions/races/${encodeURIComponent(raceId)}/evaluate`, {
          method: "POST",
          credentials: "same-origin",
        });
        if (!res.ok) throw new Error("prediction evaluation failed");
        await loadTimeline();
      }
    } catch (err) {
      const message = esc(err?.message || "展開予測の処理に失敗しました");
      root.insertAdjacentHTML("afterbegin", `<div class="start-prediction-error">${message}</div>`);
    } finally {
      clearBusy(generateButton || evaluateButton);
    }
  });

  loadTimeline().catch(() => {
    root.innerHTML = `<div class="start-prediction-empty">
      <div><b>展開予測は未生成です</b><span>展示前または展示後の予測を生成できます。</span></div>
      <div class="start-action-row">
        <button type="button" data-generate-start="pre_exhibition">展示前生成</button>
        <button type="button" data-generate-start="post_exhibition">展示後生成</button>
      </div>
    </div>`;
  });
})();
