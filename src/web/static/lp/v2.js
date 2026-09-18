var DATA = {
      nige: {claim: "「1-2-3は鉄板」", cond: "全レース・2016年6月から／3連単 1-2-3 を1点",
        stats: [{v: "81.4%", l: "回収率", t: "bad"}, {v: "7.0%", l: "的中率"}, {v: "553,814", l: "当てはまったレース"}],
        chips: [{t: "当たる", tone: "good"}, {t: "100円が81円になる", tone: "bad"}],
        note: "14回に1回は当たります。でも払い戻しは買った分の81%。競艇は売上の約25%が先に引かれる仕組みなので、「なんとなく」の買い方はだいたいこの数字に落ち着きます。"},
      omura: {claim: "「大村は満潮でイン逃げが決まる」", cond: "大村・2020年から／潮が高い時間帯と低い時間帯で比べる",
        stats: [{v: "66.4%", l: "1号艇の逃げ率（満潮付近）", t: "good"}, {v: "59.0%", l: "1号艇の逃げ率（干潮付近）"}, {v: "93% / 92%", l: "1号艇 単勝の回収率（満潮／干潮）", t: "bad"}],
        chips: [{t: "勝ち方は本当に変わる", tone: "good"}, {t: "回収率は変わらない", tone: "bad"}],
        note: "説は本当です。満潮だと1号艇がそのまま逃げ切りやすい。ただしオッズにも織り込まれていて、単勝の回収率は満潮でも干潮でもほぼ同じでした。「知っていれば儲かる」話ではありません。各約2,200レース。"},
      makuri: {claim: "「まくり屋の4号艇は、3号艇が遅いと狙い目」", cond: "4号艇のまくり率が高く、3号艇の平均スタートが遅いレース／単勝と3連単",
        stats: [{v: "19.6%", l: "4号艇の1着率（ふだんは10.3%）", t: "good"}, {v: "約1.9倍", l: "ふだんとの差"}, {v: "70〜100%", l: "4号艇 単勝の回収率", t: "bad"}],
        chips: [{t: "よく当たる", tone: "good"}, {t: "儲けにはならない", tone: "bad"}],
        note: "4号艇が来る確率はほぼ2倍。説は正しい。でも単勝の回収率はいちばん良い条件でも100%前後、3連単の4艇ボックスでも62〜80%でした。「来るのに儲からない」典型で、買うより「レースを選ぶ目安」に向いています。"},
      entry: {claim: "「前づけが多いレースは荒れる」", cond: "2〜6号艇の進入変更リスク（前日までの1年）で分ける／3連単 1-2-3",
        stats: [{v: "86.0%", l: "回収率・リスク10%以下（25,396レース）", t: "good"}, {v: "79.7%", l: "回収率・全体（146,983レース）"}, {v: "63.6%", l: "回収率・リスク40%以上（6,489レース）", t: "bad"}],
        chips: [{t: "本当に荒れる", tone: "good"}, {t: "避けると+6ポイント", tone: "warn"}],
        note: "本当です。前づけが多そうなレースを避けるだけで、1-2-3の回収率は+6ポイント。24会場中18会場、4年連続でプラスでした。ただし100%には届きませんし、単勝には効きません（オッズに織り込み済み）。"},
      kanchou: {claim: "「潮が引いた丸亀の逃げ屋は、1-2-3で決まる」", cond: "丸亀・江戸川 × 干潮前後 × 1号艇の決まり手「逃げ」60%以上／3連単 1-2-3 を1点／2025年5月から（潮のデータがある直近約16か月）",
        stats: [{v: "117.4%", l: "回収率", t: "good"}, {v: "11.5%", l: "的中率"}, {v: "約530", l: "当てはまったレース（少なめ）", t: "warn"}],
        chips: [{t: "100%を超えた", tone: "good"}, {t: "3つ重なった時だけ", tone: "warn"}, {t: "期間が短く、ゆれが大きい", tone: "warn"}],
        note: "数少ない100%超え。ただし会場・潮・逃げの3つが重なった時だけで、1つ外すと64〜91%に落ちます。期間を半分に分けると前半78%・後半157%と大きくゆれていて、まだ「続くかどうかを追いかけている」段階です。今後を保証するものではありません。"}
    };
/* Public, existing LP examples. No live query or personal data. */
const headlines = {
  nige: 'よく出る買い目でも、利益が残るとは限らない。',
  omura: '満潮では逃げ率が高い。でも、回収率はほぼ同じ。',
  makuri: '1着率は約1.9倍。回収率は別に確かめよう。',
  entry: '進入変更のリスクで、回収率にも差が出る。',
  kanchou: '過去の回収率は100%超。ただし、まだ検証途中。'
};
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nextSteps = {
  nige: '全レースで同じ買い方をする結果を確認したら、会場や風で条件を分けて比較。自分の買い方が、どんな条件で変わるか確かめます。',
  omura: '逃げ率だけで選ばず、回収率も一緒に確認。気になる会場と潮を指定して、同じ買い目で比較してみましょう。',
  makuri: '選手の特徴と周囲のスタートを組み合わせて検証。該当する条件を保存し、今日どのレースが当てはまるかを確認します。',
  entry: '進入変更のリスクを分けると回収率は86.0%・79.7%・63.6%。条件による違いを見たうえで、自分の会場や買い目でも比較してみましょう。',
  kanchou: '約530件では期間による振れも大きいため、過去の好成績だけで判断しないことが大切です。条件を保存し、その後も同じ傾向が続くか追います。'
};
function renderExample(key) {
  const example = DATA[key];
  if (!example) return;
  document.getElementById('try-result').innerHTML = `<p class="result-label">検証結果 / ${esc(example.claim)}</p><h3>${esc(headlines[key])}</h3><div class="stats">${example.stats.map(s => `<div><b class="${s.t === 'bad' ? 'bad' : ''}">${esc(s.v)}</b><span>${esc(s.l)}</span></div>`).join('')}</div><p class="result-note">${esc(example.note)}</p><div class="next-action"><b>この結果を、どう使う？</b><p>${esc(nextSteps[key])}</p><a href="/signup-supabase">自分の条件で比較する（無料登録） →</a></div><details><summary>集計条件を確認する</summary><p>${esc(example.cond)}</p><p>既存の公開ページに掲載された検証例です。現在のレースを再集計した結果ではありません。</p></details>`;
  document.querySelectorAll('.pick').forEach(button => {
    const selected = button.dataset.key === key;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}
document.querySelectorAll('.pick').forEach(button => button.addEventListener('click', () => renderExample(button.dataset.key)));
renderExample('entry');
let mangaPage = 1;
function showManga(page) {
  mangaPage = Math.max(1, Math.min(2, page));
  const picture = document.getElementById('manga-image');
  picture.src = `/static/lp/manga_p${mangaPage}.webp`;
  picture.alt = mangaPage === 1 ? 'マンガ1ページ目。よく出る買い目は本当に鉄板なのか、過去のレースで検証します。' : 'マンガ2ページ目。3連単1-2-3の的中率7%、回収率81%。過去の結果を確かめ、自分の条件を検証します。';
  document.getElementById('manga-page').textContent = `${mangaPage} / 2`;
  document.getElementById('manga-prev').disabled = mangaPage === 1;
  document.getElementById('manga-next').disabled = mangaPage === 2;
}
document.getElementById('manga-prev').addEventListener('click', () => showManga(mangaPage - 1));
document.getElementById('manga-next').addEventListener('click', () => showManga(mangaPage + 1));

if (typeof window !== 'undefined') {
  const mangaBox = document.getElementById('manga-image').closest('.manga');
  const mangaToggle = document.getElementById('manga-toggle');
  const mangaMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let mangaPlaying = !mangaMotion.matches, mangaVisible = false, mangaTimer = null;
  function syncManga() {
    if (mangaTimer !== null) { clearInterval(mangaTimer); mangaTimer = null; }
    mangaToggle.textContent = mangaPlaying ? '一時停止' : '自動めくり';
    mangaToggle.setAttribute('aria-label', mangaPlaying ? 'マンガの自動めくりを一時停止' : 'マンガを自動でめくる');
    if (mangaPlaying && mangaVisible && !document.hidden) mangaTimer = setInterval(() => showManga(mangaPage === 1 ? 2 : 1), 4000);
  }
  ['manga-prev', 'manga-next'].forEach(id => document.getElementById(id).addEventListener('click', () => { mangaPlaying = false; syncManga(); }));
  mangaToggle.addEventListener('click', () => { mangaPlaying = !mangaPlaying; syncManga(); });
  document.addEventListener('visibilitychange', syncManga);
  mangaMotion.addEventListener('change', () => { if (mangaMotion.matches) { mangaPlaying = false; syncManga(); } });
  if ('IntersectionObserver' in window) new IntersectionObserver(entries => { mangaVisible = entries[0].isIntersecting; syncManga(); }, {threshold: .35}).observe(mangaBox);
  else mangaVisible = true;
  syncManga();
}


const menuButton = document.getElementById('menu-toggle');
const mainNav = document.getElementById('main-nav');
function setMenu(open) {
  menuButton.setAttribute('aria-expanded', String(open));
  mainNav.classList.toggle('open', open);
  menuButton.querySelector('span').textContent = open ? '−' : '＋';
}
menuButton.addEventListener('click', () => setMenu(menuButton.getAttribute('aria-expanded') !== 'true'));
mainNav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => setMenu(false)));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && menuButton.getAttribute('aria-expanded') === 'true') {
    setMenu(false);
    menuButton.focus();
  }
});
function setupWalkthrough(root) {
  const frames = Array.from(root.querySelectorAll('.walk-frame'));
  const steps = Array.from(root.querySelectorAll('.walk-steps button'));
  const toggle = root.querySelector('.walk-toggle');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let current = 0, playing = !reduced.matches, visible = false, timer = null;
  function show(index) {
    current = (index + frames.length) % frames.length;
    frames.forEach((frame, i) => { frame.hidden = i !== current; });
    steps.forEach((step, i) => step.setAttribute('aria-pressed', String(i === current)));
  }
  function sync() {
    if (timer !== null) { clearInterval(timer); timer = null; }
    toggle.textContent = playing ? '一時停止' : '自動再生';
    toggle.setAttribute('aria-label', playing ? '操作説明の自動再生を一時停止' : '操作説明を自動再生');
    if (playing && visible && !document.hidden) timer = setInterval(() => show(current + 1), 6000);
  }
  steps.forEach((step, index) => step.addEventListener('click', () => { playing = false; show(index); sync(); }));
  toggle.addEventListener('click', () => { playing = !playing; sync(); });
  document.addEventListener('visibilitychange', sync);
  reduced.addEventListener('change', () => { if (reduced.matches) { playing = false; sync(); } });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(entries => { visible = entries[0].isIntersecting; sync(); }, {threshold: 0.25}).observe(root);
  } else { visible = true; }
  show(0); sync();
}
if (typeof window !== 'undefined') document.querySelectorAll('.walkthrough').forEach(setupWalkthrough);
