(() => {
  const root = document.querySelector("[data-start-prediction]");
  if (!root) return;

  const raceId = root.dataset.raceId;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const pct = (v) => `${(Number(v || 0) * 100).toFixed(1)}%`;
  const num = (v, d = 3) => (v == null || v === "" || Number.isNaN(Number(v)) ? "-" : Number(v).toFixed(d));
  const stText = (v) => {
    if (v == null || v === "" || Number.isNaN(Number(v))) return "-";
    return Number(v).toFixed(2).replace(/^0/, "");
  };
  const lane = (n) => `<span class="lane lane-${esc(n)}">${esc(n)}</span>`;
  const boatIcon = (n) => `<span class="start-boat-icon start-boat-${esc(n)}" aria-label="${esc(n)}号艇"></span>`;
  const stageLabel = { pre_exhibition: "展示前", post_exhibition: "展示後" };
  const stageTitle = {
    pre_exhibition: "展示前スタート仮説",
    post_exhibition: "スタート展示補正",
  };
  const stageSub = {
    pre_exhibition: "展示前に見える選手・モーター・天候・潮から予測",
    post_exhibition: "展示進入・展示ST・展示タイムで直前補正",
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

  const boatOffset = (row, index) => {
    const rank = Number(row.predicted_start_rank || index + 1);
    const top = Number(row.start_top_probability || 0);
    const st = Number(row.predicted_st || 0.16);
    const raw = 68 - rank * 7 + top * 22 - Math.max(0, st - 0.12) * 120;
    return Math.max(18, Math.min(78, raw));
  };

  const renderStartExhibitionPanel = (prediction, actual) => {
    const boats = prediction?.boats?.length
      ? [...prediction.boats].sort((a, b) => (
        Number(a.entry_course || a.course_number || a.boat_number) -
        Number(b.entry_course || b.course_number || b.boat_number)
      ))
      : [1, 2, 3, 4, 5, 6].map((boat) => ({ boat_number: boat }));
    const resultByBoat = actualMap(actual);
    return `<div class="start-exhibition-board">
      <div class="start-exhibition-title">スタート展示</div>
      <div class="start-exhibition-head">
        <span>コース</span><span>並び</span><span>ST</span>
      </div>
      <div class="start-water-grid">
        ${boats.map((b, idx) => {
          const boat = Number(b.boat_number);
          const course = Number(b.entry_course || b.course_number || boat);
          const result = resultByBoat[boat];
          const offset = boatOffset(b, idx);
          const isTop = Number(b.predicted_start_rank || 9) === 1;
          const finish = result?.finishing_position ? `${result.finishing_position}着` : "";
          return `<div class="start-water-row${isTop ? " is-top" : ""}">
            <div class="start-course-cell">${lane(course)}</div>
            <div class="start-lane-water">
              <span class="start-water-line"></span>
              <span class="start-boat-position" style="left:${offset}%">${boatIcon(boat)}</span>
            </div>
            <div class="start-st-cell">
              <b>${stText(b.predicted_st)}</b>
              <small>${pct(b.start_top_probability)}</small>
              ${finish ? `<em>${esc(finish)}</em>` : ""}
            </div>
          </div>`;
        }).join("")}
      </div>
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
      <div class="start-stage-title"><span>${esc(stageTitle[stage])}</span><b>信頼度 ${pct(prediction.confidence)}</b></div>
      <p>${esc(stageSub[stage])}</p>
      ${renderStartExhibitionPanel(prediction, actual)}
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
    const actualPrediction = {
      boats: [1, 2, 3, 4, 5, 6].map((boat) => {
        const result = resultByBoat[boat] || {};
        return {
          boat_number: boat,
          entry_course: result.course_number || boat,
          predicted_st: result.start_timing,
          predicted_start_rank: result.start_timing == null ? 9 : null,
          start_top_probability: boat === Number(actual.actual_start_top_boat) ? 1 : 0,
        };
      }).sort((a, b) => Number(a.boat_number) - Number(b.boat_number)),
    };
    actualPrediction.boats
      .filter((b) => b.predicted_st != null)
      .sort((a, b) => Number(a.predicted_st) - Number(b.predicted_st))
      .forEach((b, i) => { b.predicted_start_rank = i + 1; });
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
      ${renderStartExhibitionPanel(actualPrediction, actual)}
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
        <p>展示前、展示後、本番結果をスタート展示形式で比較します。</p>
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
    <div class="start-comparison-note">艇の前後位置は予測ST順位、STトップ確率、予測STから算定した視覚表現です。公式の展示隊形そのものではなく、傾向比較用のモデル表示です。</div>`;
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
    const target = event.target instanceof Element ? event.target : null;
    const generateButton = target?.closest("[data-generate-start]");
    const evaluateButton = target?.closest("[data-evaluate-start]");
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
